"""
src/db.py

DuckDB schema and I/O helpers for the BigThink pipeline.

Structure follows Tripwire's ingestion/db.py (one module owns the schema, a
context-managed connection, explicit upsert helpers, a run-log table used by
observability). The engine differs: Tripwire monitors and alerts, so SQLite's
row-at-a-time access suited it. BigThink aggregates — group-bys over hundreds
of thousands of documents per stage — which is what DuckDB is for, and what
BasicInfraSuggestion.md specifies.

Everything the pipeline learns lives here. Stages never pass Python objects to
each other; each stage reads its inputs from DuckDB and writes its outputs
back. That is what makes a stage re-runnable in isolation and what makes a
week-old result explainable.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Iterable, Sequence

import duckdb

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
# DuckDB has no AUTOINCREMENT; sequences serve the same purpose.

SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS seq_run_id START 1;

-- Stage 1 output: one row per collected document, deduplicated on doc_id.
CREATE TABLE IF NOT EXISTS documents (
    doc_id           VARCHAR PRIMARY KEY,   -- sha256 of (source, native id)
    source           VARCHAR NOT NULL,      -- openalex | arxiv | crossref | gdelt | ...
    native_id        VARCHAR,               -- DOI, arXiv id, patent number, URL
    title            VARCHAR,
    abstract         VARCHAR,
    published_date   DATE,
    year             INTEGER,
    time_slice       VARCHAR,               -- '2024' or '2024Q3'
    url              VARCHAR,
    venue            VARCHAR,
    authors          VARCHAR,               -- JSON array
    institutions     VARCHAR,               -- JSON array; feeds Rotolo 'uncertainty'
    concepts         VARCHAR,               -- JSON array of source-native subject tags
    citation_count   INTEGER DEFAULT 0,
    steepv           VARCHAR,               -- STEEPV category
    scan_frame_key   VARCHAR,               -- which seed query found it
    tone             DOUBLE,                -- GDELT tone; NULL elsewhere
    raw_path         VARCHAR,               -- relative path to the raw payload
    collected_at     TIMESTAMP NOT NULL,
    run_id           VARCHAR NOT NULL
);

-- Cached embedding vectors, keyed by content hash so a re-run of a stable
-- corpus does not re-embed it (and so switching backend invalidates cleanly).
CREATE TABLE IF NOT EXISTS vectors (
    content_hash     VARCHAR NOT NULL,
    backend          VARCHAR NOT NULL,
    dimensions       INTEGER NOT NULL,
    vector           DOUBLE[] NOT NULL,
    created_at       TIMESTAMP NOT NULL,
    PRIMARY KEY (content_hash, backend)
);

-- Stage 0 output: the strategic reference set.
CREATE TABLE IF NOT EXISTS strategy_refs (
    ref_id           VARCHAR PRIMARY KEY,
    ref_type         VARCHAR NOT NULL,      -- objective | initiative | critical_tech | asset
    code             VARCHAR,               -- e.g. '4.2' for Strategic Objective 4.2
    label            VARCHAR NOT NULL,
    text             VARCHAR NOT NULL,
    lexicon          VARCHAR,               -- JSON array of keyword terms
    weight           DOUBLE DEFAULT 1.0,
    source_document  VARCHAR,
    created_at       TIMESTAMP NOT NULL
);

-- Stage 2 output: one row per detected topic.
CREATE TABLE IF NOT EXISTS topics (
    topic_id         VARCHAR PRIMARY KEY,
    run_id           VARCHAR NOT NULL,
    label            VARCHAR,               -- top terms, human-readable
    terms            VARCHAR,               -- JSON array of (term, weight)
    document_count   INTEGER,
    first_slice      VARCHAR,
    last_slice       VARCHAR,
    -- Rotolo et al. (2015) five attributes, each normalised to [0, 1]
    novelty          DOUBLE,
    growth           DOUBLE,
    coherence        DOUBLE,
    impact           DOUBLE,
    uncertainty      DOUBLE,
    emergence_score  DOUBLE,                -- weighted combination of the five
    burst_weight     DOUBLE,                -- Kleinberg max burst intensity
    burst_slices     VARCHAR,               -- JSON array of slices in burst state
    cagr             DOUBLE,
    maturity         DOUBLE,                -- fitted logistic position in [0, 1]
    horizon          VARCHAR,               -- H1 | H2 | H3
    signal_class     VARCHAR,               -- weak | strong | latent | noise
    avg_proportion   DOUBLE,
    created_at       TIMESTAMP NOT NULL
);

-- Topic membership, so every topic-level score is traceable to documents.
CREATE TABLE IF NOT EXISTS topic_documents (
    topic_id         VARCHAR NOT NULL,
    doc_id           VARCHAR NOT NULL,
    similarity       DOUBLE,
    PRIMARY KEY (topic_id, doc_id)
);

-- Per-slice counts, the input to burst detection and growth fitting.
CREATE TABLE IF NOT EXISTS topic_timeseries (
    topic_id         VARCHAR NOT NULL,
    time_slice       VARCHAR NOT NULL,
    doc_count        INTEGER NOT NULL,
    proportion       DOUBLE,
    in_burst         BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (topic_id, time_slice)
);

-- Stages 3 and 4: scores kept separate from topics so a rescore does not
-- rewrite Stage 2 output. Re-running Stage 3 must never mutate emergence.
CREATE TABLE IF NOT EXISTS topic_scores (
    topic_id             VARCHAR NOT NULL,
    run_id               VARCHAR NOT NULL,
    strategic_fit        DOUBLE,
    best_objective       VARCHAR,   -- highest-similarity strategy ref
    best_objective_sim   DOUBLE,
    critical_tech        VARCHAR,   -- matched DISR field, if any
    asset_leverage       DOUBLE,
    best_asset           VARCHAR,
    opportunity_index    DOUBLE,    -- RELATIVE index; never a dollar value
    index_components     VARCHAR,   -- JSON of component percentiles
    index_suppressed     BOOLEAN DEFAULT FALSE,  -- true when below min_documents
    composite_rank_score DOUBLE,
    rank                 INTEGER,
    created_at           TIMESTAMP NOT NULL,
    PRIMARY KEY (topic_id, run_id)
);

-- One row per stage execution. The observability surface: what ran, over what,
-- how long it took, under which config.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id               INTEGER PRIMARY KEY DEFAULT nextval('seq_run_id'),
    run_id           VARCHAR NOT NULL,
    stage            VARCHAR NOT NULL,
    status           VARCHAR NOT NULL,      -- success | partial | failed
    started_at       TIMESTAMP NOT NULL,
    finished_at      TIMESTAMP,
    records_in       INTEGER,
    records_out      INTEGER,
    message          VARCHAR,
    config_snapshot  VARCHAR
);

-- Per-source outcome within a collection run, so a silently-dead collector is
-- visible rather than showing up as "fewer results this week".
CREATE TABLE IF NOT EXISTS collection_log (
    run_id           VARCHAR NOT NULL,
    source           VARCHAR NOT NULL,
    scan_frame_key   VARCHAR NOT NULL,
    status           VARCHAR NOT NULL,      -- success | skipped | failed
    records          INTEGER DEFAULT 0,
    message          VARCHAR,
    logged_at        TIMESTAMP NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def init_db(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) the DuckDB database and ensure the schema."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    conn.execute(SCHEMA)
    return conn


@contextlib.contextmanager
def get_connection(db_path: str | Path) -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Context-managed connection that always closes."""
    conn = init_db(db_path)
    try:
        yield conn
    finally:
        conn.close()


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

_DOCUMENT_COLUMNS = (
    "doc_id", "source", "native_id", "title", "abstract", "published_date",
    "year", "time_slice", "url", "venue", "authors", "institutions", "concepts",
    "citation_count", "steepv", "scan_frame_key", "tone", "raw_path",
    "collected_at", "run_id",
)


def upsert_documents(
    conn: duckdb.DuckDBPyConnection, docs: Sequence[dict[str, Any]]
) -> int:
    """Insert documents, ignoring any doc_id already present.

    Returns the number of genuinely new rows. Re-collection is expected — the
    same paper surfaces under several seed queries — so the count of new rows,
    not the count of fetched records, is what a run should report.
    """
    if not docs:
        return 0

    before = count_documents(conn)
    rows = [tuple(_coerce(d.get(col)) for col in _DOCUMENT_COLUMNS) for d in docs]
    placeholders = ", ".join("?" * len(_DOCUMENT_COLUMNS))
    conn.executemany(
        f"INSERT OR IGNORE INTO documents ({', '.join(_DOCUMENT_COLUMNS)}) "
        f"VALUES ({placeholders})",
        rows,
    )
    return count_documents(conn) - before


def count_documents(conn: duckdb.DuckDBPyConnection) -> int:
    return int(conn.execute("SELECT count(*) FROM documents").fetchone()[0])


def fetch_documents(
    conn: duckdb.DuckDBPyConnection,
    *,
    sources: Iterable[str] | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch documents as dicts, optionally filtered."""
    clauses, params = [], []
    if sources:
        srcs = list(sources)
        clauses.append(f"source IN ({', '.join('?' * len(srcs))})")
        params.extend(srcs)
    if min_year is not None:
        clauses.append("year >= ?")
        params.append(min_year)
    if max_year is not None:
        clauses.append("year <= ?")
        params.append(max_year)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    cur = conn.execute(f"SELECT * FROM documents{where} ORDER BY published_date", params)
    return _rows_to_dicts(cur)


# ---------------------------------------------------------------------------
# Vectors
# ---------------------------------------------------------------------------


def get_cached_vectors(
    conn: duckdb.DuckDBPyConnection, backend: str, hashes: Sequence[str]
) -> dict[str, list[float]]:
    if not hashes:
        return {}
    placeholders = ", ".join("?" * len(hashes))
    cur = conn.execute(
        f"SELECT content_hash, vector FROM vectors "
        f"WHERE backend = ? AND content_hash IN ({placeholders})",
        [backend, *hashes],
    )
    return {row[0]: list(row[1]) for row in cur.fetchall()}


def store_vectors(
    conn: duckdb.DuckDBPyConnection,
    backend: str,
    dimensions: int,
    vectors: dict[str, Sequence[float]],
) -> None:
    if not vectors:
        return
    ts = now()
    conn.executemany(
        "INSERT OR REPLACE INTO vectors (content_hash, backend, dimensions, vector, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(h, backend, dimensions, list(map(float, v)), ts) for h, v in vectors.items()],
    )


# ---------------------------------------------------------------------------
# Strategy references (Stage 0)
# ---------------------------------------------------------------------------


def replace_strategy_refs(
    conn: duckdb.DuckDBPyConnection, refs: Sequence[dict[str, Any]]
) -> int:
    """Replace the whole strategic reference set.

    Wholesale replacement, not upsert: Stage 0 is cheap and its output must
    match the strategy documents exactly. A stale objective left behind from a
    superseded corporate plan would quietly skew every fit score downstream.
    """
    conn.execute("DELETE FROM strategy_refs")
    if not refs:
        return 0
    ts = now()
    conn.executemany(
        "INSERT INTO strategy_refs "
        "(ref_id, ref_type, code, label, text, lexicon, weight, source_document, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                r["ref_id"], r["ref_type"], r.get("code"), r["label"], r["text"],
                json.dumps(r.get("lexicon", [])), float(r.get("weight", 1.0)),
                r.get("source_document"), ts,
            )
            for r in refs
        ],
    )
    return len(refs)


def fetch_strategy_refs(
    conn: duckdb.DuckDBPyConnection, ref_type: str | None = None
) -> list[dict[str, Any]]:
    if ref_type:
        cur = conn.execute(
            "SELECT * FROM strategy_refs WHERE ref_type = ? ORDER BY code, ref_id", [ref_type]
        )
    else:
        cur = conn.execute("SELECT * FROM strategy_refs ORDER BY ref_type, code, ref_id")
    refs = _rows_to_dicts(cur)
    for r in refs:
        r["lexicon"] = json.loads(r["lexicon"]) if r.get("lexicon") else []
    return refs


# ---------------------------------------------------------------------------
# Topics (Stage 2)
# ---------------------------------------------------------------------------

_TOPIC_COLUMNS = (
    "topic_id", "run_id", "label", "terms", "document_count", "first_slice",
    "last_slice", "novelty", "growth", "coherence", "impact", "uncertainty",
    "emergence_score", "burst_weight", "burst_slices", "cagr", "maturity",
    "horizon", "signal_class", "avg_proportion", "created_at",
)


def replace_topics(
    conn: duckdb.DuckDBPyConnection, run_id: str, topics: Sequence[dict[str, Any]]
) -> int:
    """Replace all topics for *run_id* along with their membership and series."""
    conn.execute(
        "DELETE FROM topic_documents WHERE topic_id IN "
        "(SELECT topic_id FROM topics WHERE run_id = ?)", [run_id]
    )
    conn.execute(
        "DELETE FROM topic_timeseries WHERE topic_id IN "
        "(SELECT topic_id FROM topics WHERE run_id = ?)", [run_id]
    )
    conn.execute("DELETE FROM topics WHERE run_id = ?", [run_id])
    if not topics:
        return 0

    ts = now()
    conn.executemany(
        f"INSERT INTO topics ({', '.join(_TOPIC_COLUMNS)}) "
        f"VALUES ({', '.join('?' * len(_TOPIC_COLUMNS))})",
        [
            tuple(
                ts if col == "created_at"
                else run_id if col == "run_id"
                else _coerce(t.get(col))
                for col in _TOPIC_COLUMNS
            )
            for t in topics
        ],
    )

    members = [
        (t["topic_id"], d["doc_id"], float(d.get("similarity", 0.0)))
        for t in topics
        for d in t.get("documents", [])
    ]
    if members:
        conn.executemany(
            "INSERT OR REPLACE INTO topic_documents (topic_id, doc_id, similarity) "
            "VALUES (?, ?, ?)",
            members,
        )

    series = [
        (t["topic_id"], s["time_slice"], int(s["doc_count"]),
         float(s.get("proportion", 0.0)), bool(s.get("in_burst", False)))
        for t in topics
        for s in t.get("timeseries", [])
    ]
    if series:
        conn.executemany(
            "INSERT OR REPLACE INTO topic_timeseries "
            "(topic_id, time_slice, doc_count, proportion, in_burst) VALUES (?, ?, ?, ?, ?)",
            series,
        )
    return len(topics)


def fetch_topics(conn: duckdb.DuckDBPyConnection, run_id: str) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM topics WHERE run_id = ? ORDER BY emergence_score DESC", [run_id]
    )
    topics = _rows_to_dicts(cur)
    for t in topics:
        t["terms"] = json.loads(t["terms"]) if t.get("terms") else []
        t["burst_slices"] = json.loads(t["burst_slices"]) if t.get("burst_slices") else []
    return topics


def fetch_topic_documents(
    conn: duckdb.DuckDBPyConnection, topic_id: str, limit: int = 10
) -> list[dict[str, Any]]:
    """Top documents for a topic — the evidence behind its scores."""
    cur = conn.execute(
        """
        SELECT d.*, td.similarity
        FROM topic_documents td
        JOIN documents d USING (doc_id)
        WHERE td.topic_id = ?
        ORDER BY td.similarity DESC, d.citation_count DESC
        LIMIT ?
        """,
        [topic_id, limit],
    )
    return _rows_to_dicts(cur)


def fetch_topic_timeseries(
    conn: duckdb.DuckDBPyConnection, topic_id: str
) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM topic_timeseries WHERE topic_id = ? ORDER BY time_slice", [topic_id]
    )
    return _rows_to_dicts(cur)


# ---------------------------------------------------------------------------
# Scores (Stages 3-5)
# ---------------------------------------------------------------------------

_SCORE_COLUMNS = (
    "topic_id", "run_id", "strategic_fit", "best_objective", "best_objective_sim",
    "critical_tech", "asset_leverage", "opportunity_index", "index_components",
    "index_suppressed", "composite_rank_score", "rank", "created_at",
)


def replace_topic_scores(
    conn: duckdb.DuckDBPyConnection, run_id: str, scores: Sequence[dict[str, Any]]
) -> int:
    conn.execute("DELETE FROM topic_scores WHERE run_id = ?", [run_id])
    if not scores:
        return 0
    ts = now()
    conn.executemany(
        f"INSERT INTO topic_scores ({', '.join(_SCORE_COLUMNS)}) "
        f"VALUES ({', '.join('?' * len(_SCORE_COLUMNS))})",
        [
            tuple(
                ts if col == "created_at"
                else run_id if col == "run_id"
                else _coerce(s.get(col))
                for col in _SCORE_COLUMNS
            )
            for s in scores
        ],
    )
    return len(scores)


def fetch_ranked_topics(
    conn: duckdb.DuckDBPyConnection, run_id: str, limit: int | None = None
) -> list[dict[str, Any]]:
    """Topics joined to their scores, in rank order — the Stage 5 view."""
    sql = """
        SELECT t.*, s.strategic_fit, s.best_objective, s.best_objective_sim,
               s.critical_tech, s.asset_leverage, s.opportunity_index,
               s.index_components, s.index_suppressed, s.composite_rank_score, s.rank
        FROM topics t
        JOIN topic_scores s USING (topic_id, run_id)
        WHERE t.run_id = ?
        ORDER BY s.rank
    """
    params: list[Any] = [run_id]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = _rows_to_dicts(conn.execute(sql, params))
    for r in rows:
        r["terms"] = json.loads(r["terms"]) if r.get("terms") else []
        r["burst_slices"] = json.loads(r["burst_slices"]) if r.get("burst_slices") else []
        r["index_components"] = (
            json.loads(r["index_components"]) if r.get("index_components") else {}
        )
    return rows


# ---------------------------------------------------------------------------
# Run logging
# ---------------------------------------------------------------------------


def log_stage_start(
    conn: duckdb.DuckDBPyConnection, run_id: str, stage: str, config_snapshot: str = ""
) -> int:
    cur = conn.execute(
        "INSERT INTO pipeline_runs (run_id, stage, status, started_at, config_snapshot) "
        "VALUES (?, ?, 'running', ?, ?) RETURNING id",
        [run_id, stage, now(), config_snapshot],
    )
    return int(cur.fetchone()[0])


def log_stage_finish(
    conn: duckdb.DuckDBPyConnection,
    entry_id: int,
    status: str,
    *,
    records_in: int | None = None,
    records_out: int | None = None,
    message: str = "",
) -> None:
    conn.execute(
        "UPDATE pipeline_runs SET status = ?, finished_at = ?, records_in = ?, "
        "records_out = ?, message = ? WHERE id = ?",
        [status, now(), records_in, records_out, message[:2000], entry_id],
    )


def log_collection(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    source: str,
    scan_frame_key: str,
    status: str,
    records: int = 0,
    message: str = "",
) -> None:
    conn.execute(
        "INSERT INTO collection_log "
        "(run_id, source, scan_frame_key, status, records, message, logged_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [run_id, source, scan_frame_key, status, records, message[:1000], now()],
    )


def collection_summary(conn: duckdb.DuckDBPyConnection, run_id: str) -> list[dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT source, status, count(*) AS queries, sum(records) AS records
        FROM collection_log WHERE run_id = ?
        GROUP BY source, status ORDER BY source, status
        """,
        [run_id],
    )
    return _rows_to_dicts(cur)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rows_to_dicts(cur: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _coerce(value: Any) -> Any:
    """JSON-encode lists/dicts so they round-trip through VARCHAR columns."""
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return value
