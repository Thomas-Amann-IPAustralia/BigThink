"""
src/dashboard.py — build the interactive explorer published to GitHub Pages.

Renders docs/dashboard.html: a five-view analytical tool over one finished run.

    Method   docs/method.md made interactive and grounded in this run's numbers
    Map      every collected document as a 2D point cloud, with a fidelity readout
    Topics   every topic and every score, sortable, filterable, expandable
    Scores   any score against any other, with the meaningful pairs preset
    Data     the run's tables, browsable and exportable

Self-contained, like src/report.py — one HTML file with the data and every
line of CSS and JS inlined, no CDN and no build step, because it is served
from GitHub Pages and a page that silently fails to render behind a corporate
proxy is worse than a plain one.

WHY THE ASSETS LIVE IN src/dashboard_assets/ RATHER THAN IN THIS FILE. The
published page is still one self-contained file — the assets are read and
inlined at build time. What moved is the *source*: a few thousand lines of CSS
and JS inside a Python string cannot be linted, folded, or diffed usefully, and
this page is now the main way anyone reads a run. The "no CDN" decision is
about what the browser fetches, and that is unchanged.

WHERE THE POINTS COME FROM. Stage 2 embeds every document to cluster it, but
only persists the vectors when the backend is cacheable (`bge`; `hashing` is
cheap enough that persisting it would cost more in database writes than it
saves — see src/embeddings.py). So this module re-embeds the corpus with the
same embedder and the same cache the stages use, rather than reading stored
vectors that usually are not there. Topic membership itself — which this
module does not recompute — comes straight from `topic_documents`.

WHAT THE MAP DOES NOT MEAN, AND HOW THE PAGE PROVES IT. UMAP and PCA both
produce axes with no inherent meaning, and both distort: a 384-dimensional
neighbourhood cannot survive being flattened to two intact. The honest
response is not a disclaimer, it is a measurement, so this module computes
and ships four of them —

    trustworthiness   are the neighbours you can see real?
    continuity        are the real neighbours visible?
    neighbour purity  per topic, in 384-D and in 2D, so a topic the map tears
                      apart says so on its own row
    the k nearest high-dimensional neighbours of every plotted point, so a
                      reader can select a document and watch where its true
                      neighbours actually landed

The projection also follows the clustering configuration by default
(`dashboard.projection.follow_clustering`), so the map is a 2-component view of
the same manifold BERTopic clustered in rather than an unrelated second
opinion.

Run:
    python -m src.dashboard --run-id 2026-08-31
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from src import db
from src.collectors.base import document_text
from src.config import (
    PAGE_HEAD,
    PAGE_TAIL,
    REPO_ROOT,
    get,
    load_config,
    repo_url as _repo_url,
    resolve_path,
)
from src.embeddings import build_embedder, encode_with_cache

logger = logging.getLogger(__name__)

ASSET_DIR = Path(__file__).resolve().parent / "dashboard_assets"

#: Concatenated in this order into one <script>. core.js defines the BT
#: namespace and every view attaches to it, so the order is load-bearing:
#: core first, boot last.
_JS_ASSETS = (
    "core.js",
    "method.js",
    "map.js",
    "topics.js",
    "scores.js",
    "data.js",
    "boot.js",
)


def _round(value: Any, places: int = 4) -> float | None:
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def _pca_2d(vectors: np.ndarray) -> np.ndarray:
    """Top-2 principal components via SVD. Numpy-only, no extra dependency.

    Used when umap-learn is not installed. UMAP separates topic clusters far
    better on this kind of high-dimensional sparse text vector; PCA still
    produces a real, honest map, just a flatter one — the same
    graceful-degradation shape src/embeddings.py uses for the bge backend.
    """
    centered = vectors - vectors.mean(axis=0, keepdims=True)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    return centered @ vt[:2].T


def projection_params(config: dict[str, Any]) -> dict[str, Any]:
    """The UMAP settings the map will use, and where each one came from.

    WHY THIS FOLLOWS THE CLUSTERING BY DEFAULT. Under `bertopic` the clusters
    were found by HDBSCAN in a UMAP space built with a particular neighbourhood
    size, metric and seed. Projecting the map with *different* settings answers
    a different question, and the disagreement shows up as a topic scattered
    across the picture for no reason a reader can see. Following the clustering
    configuration makes the map a 2-component view of the manifold the topics
    were actually found in. `follow_clustering: false` restores an independent
    projection, which is a legitimate thing to want — it is a second opinion on
    whether the clusters survive a different neighbourhood size — but it is not
    the right default for a page whose job is to show what the run decided.
    """
    projection = dict(get(config, "dashboard", "projection", default={}) or {})
    method = str(projection.get("method", "umap"))
    follow = bool(projection.get("follow_clustering", True))
    clustering = str(get(config, "emergence", "topics", "method", default="") or "")
    bertopic = get(config, "emergence", "topics", "bertopic", default={}) or {}

    params = {
        "n_neighbors": int(projection.get("n_neighbors", 15)),
        "min_dist": float(projection.get("min_dist", 0.1)),
        "metric": str(projection.get("metric", "cosine")),
        "random_state": int(projection.get("random_state", 42)),
    }
    followed: list[str] = []
    if follow and clustering == "bertopic":
        for key in ("n_neighbors", "metric", "random_state"):
            value = bertopic.get(key)
            if value is None:
                continue
            cast = str(value) if key == "metric" else int(value)
            if params[key] != cast:
                followed.append(key)
            params[key] = cast
    return {
        "method": method,
        "follow_clustering": follow,
        "clustering_method": clustering,
        # min_dist is deliberately never followed: the clustering runs it at 0.0
        # to pack clusters as tightly as HDBSCAN likes, which on a screen draws
        # every topic as an indistinguishable dot.
        "followed": followed,
        **params,
    }


def project_2d(
    vectors: np.ndarray, config: dict[str, Any]
) -> tuple[np.ndarray, str, dict[str, Any]]:
    """Reduce *vectors* to 2D. Returns (coords, method actually used, params)."""
    params = projection_params(config)
    n = vectors.shape[0]

    if params["method"] == "umap" and n >= 4:
        try:
            import umap  # noqa: PLC0415 - optional dependency, imported lazily

            reducer = umap.UMAP(
                n_neighbors=max(2, min(params["n_neighbors"], n - 1)),
                min_dist=params["min_dist"],
                metric=params["metric"],
                random_state=params["random_state"],
                n_components=2,
            )
            coords = reducer.fit_transform(np.asarray(vectors, dtype=np.float64))
            return np.asarray(coords, dtype=np.float64), "umap", params
        except ImportError:
            logger.warning(
                "umap-learn not installed; falling back to a PCA projection. "
                "pip install umap-learn (or requirements.txt) for the intended point cloud."
            )

    return _pca_2d(np.asarray(vectors, dtype=np.float64)), "pca", params


# ---------------------------------------------------------------------------
# Projection fidelity
#
# A 2D picture of a 384-dimensional space is a lossy compression, and the loss
# is not uniform: some topics survive it intact and some are torn in half. None
# of that is visible in the picture itself, which is exactly why it has to be
# measured and printed next to it.
# ---------------------------------------------------------------------------


def _unit(vectors: np.ndarray) -> np.ndarray:
    v = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(v, axis=1, keepdims=True)
    return v / np.maximum(norms, 1e-12)


def knn_indices(
    vectors: np.ndarray, k: int, metric: str = "cosine", chunk: int = 512
) -> np.ndarray:
    """Exact k nearest neighbours per row, self first. Shape (n, k + 1).

    Chunked over rows because the full similarity matrix for a 25,000-document
    corpus is 2.5 GB and this has to run on a free GitHub Actions runner.
    Exact rather than approximate: the whole point of the number it feeds is
    that a reader can trust it, and an ANN index would make the fidelity
    measurement itself an approximation of unknown quality.
    """
    n = vectors.shape[0]
    k = max(1, min(k, n - 1))
    out = np.empty((n, k + 1), dtype=np.int32)
    data = _unit(vectors) if metric == "cosine" else np.asarray(vectors, dtype=np.float32)

    squared = np.square(data).sum(axis=1) if metric != "cosine" else None
    for start in range(0, n, chunk):
        block = data[start : start + chunk]
        if metric == "cosine":
            score = block @ data.T  # higher is nearer
        else:
            # Ranking-equivalent to minus the squared euclidean distance:
            # -(|a|^2 - 2ab + |b|^2), with |a|^2 dropped because it is constant
            # along a row and cannot change that row's ordering. |b|^2 varies
            # per candidate and must stay — dropping it would rank a distant
            # large vector as a near neighbour.
            score = 2.0 * (block @ data.T) - squared[None, :]
        part = np.argpartition(-score, kth=k, axis=1)[:, : k + 1]
        rows = np.arange(part.shape[0])[:, None]
        order = np.argsort(-score[rows, part], axis=1, kind="stable")
        out[start : start + chunk] = part[rows, order]
    return out


def _rank_matrix(distance: np.ndarray) -> np.ndarray:
    """Rank of every column from every row: 0 is the row itself, 1 its nearest."""
    order = np.argsort(distance, axis=1, kind="stable")
    ranks = np.empty_like(order)
    rows = np.arange(distance.shape[0])[:, None]
    ranks[rows, order] = np.arange(distance.shape[1])[None, :]
    return ranks


def trustworthiness_continuity(
    vectors: np.ndarray, coords: np.ndarray, k: int
) -> tuple[float, float]:
    """Venna & Kaski's paired measures of how much the projection lies.

    Trustworthiness penalises points drawn *close together* that are far apart
    in the real space — the neighbours you can see that are not real, which is
    the error that invents a cluster. Continuity penalises the opposite — real
    neighbours the projection has pushed apart, which is the error that tears a
    real topic in half. Both are needed: a projection can score well on one by
    failing the other.

    Both are O(n^2) in memory, so the caller samples.
    """
    n = vectors.shape[0]
    k = max(1, min(k, (n - 1) // 3))
    if n < 5 or 2 * n - 3 * k - 1 <= 0:
        return float("nan"), float("nan")

    unit = _unit(vectors).astype(np.float64)
    d_high = 1.0 - unit @ unit.T
    np.fill_diagonal(d_high, -1.0)  # keep self strictly first
    low = np.asarray(coords, dtype=np.float64)
    d_low = np.square(low).sum(1)[:, None] - 2 * low @ low.T + np.square(low).sum(1)[None, :]
    np.fill_diagonal(d_low, -1.0)

    rank_high, rank_low = _rank_matrix(d_high), _rank_matrix(d_low)
    order_high = np.argsort(d_high, axis=1, kind="stable")[:, 1 : k + 1]
    order_low = np.argsort(d_low, axis=1, kind="stable")[:, 1 : k + 1]
    norm = 2.0 / (n * k * (2 * n - 3 * k - 1))

    trust = 1.0 - norm * np.clip(
        np.take_along_axis(rank_high, order_low, axis=1) - k, 0, None
    ).sum()
    cont = 1.0 - norm * np.clip(
        np.take_along_axis(rank_low, order_high, axis=1) - k, 0, None
    ).sum()
    return float(trust), float(cont)


def neighbour_purity(neighbours: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Per point, the share of its k nearest neighbours carrying its own label.

    Run over the same points in 384-D and in 2D, the pair answers the question
    a reader actually has about a scattered-looking topic: is it scattered
    because the topic is incoherent, or because the projection tore it up?
    """
    nn = neighbours[:, 1:]
    return (labels[nn] == labels[:, None]).mean(axis=1)


def _convex_hull(points: np.ndarray) -> list[list[float]]:
    """Monotone-chain hull. Numpy only — scipy is not a dependency here."""
    if len(points) < 3:
        return [[float(x), float(y)] for x, y in points]
    pts = np.unique(points, axis=0)
    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]
    if len(pts) < 3:
        return [[float(x), float(y)] for x, y in pts]

    def half(seq: np.ndarray) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for p in seq:
            while len(out) >= 2:
                (ax, ay), (bx, by) = out[-2], out[-1]
                if (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) > 0:
                    break
                out.pop()
            out.append(p)
        return out

    hull = half(pts)[:-1] + half(pts[::-1])[:-1]
    # Four places, matching the plotted coordinates: every hull vertex is one
    # of the points, and rounding it to a coarser grid would draw the outline
    # fractionally off its own cluster.
    return [[round(float(p[0]), 4), round(float(p[1]), 4)] for p in hull]


def topic_hull(coords: np.ndarray, trim: float = 0.1) -> list[list[float]]:
    """A hull around a topic's members, after trimming the furthest *trim*.

    Untrimmed, one stray member — and average-linkage clustering produces them —
    stretches the hull across the whole map and the shape stops meaning
    anything. Trimming to the densest 90% draws where the topic actually is.
    """
    if len(coords) < 3:
        return _convex_hull(coords)
    centre = np.median(coords, axis=0)
    dist = np.linalg.norm(coords - centre, axis=1)
    keep = coords[dist <= np.quantile(dist, 1.0 - trim)]
    return _convex_hull(keep if len(keep) >= 3 else coords)


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------


def _select_points(
    documents: list[dict[str, Any]],
    assigned: set[str],
    max_points: int,
    run_id: str,
) -> list[int]:
    """Indices of documents to plot, downsampled deterministically if needed.

    Every topic-assigned document is kept first — that is the signal worth
    exploring — and the budget left over, if any, is filled with a random
    sample of the rest (mostly GDELT, which forms no topics; see
    emergence.topics.forming_sources). Seeded from run_id so a re-render of
    the same run reproduces the same sample rather than jittering pointlessly.
    """
    n = len(documents)
    if n <= max_points:
        return list(range(n))

    seed = int(hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)

    assigned_idx = [i for i, d in enumerate(documents) if d["doc_id"] in assigned]
    other_idx = [i for i, d in enumerate(documents) if d["doc_id"] not in assigned]

    if len(assigned_idx) >= max_points:
        keep = rng.choice(np.array(assigned_idx), size=max_points, replace=False)
    else:
        budget = max_points - len(assigned_idx)
        extra = (
            rng.choice(np.array(other_idx), size=min(budget, len(other_idx)), replace=False)
            if other_idx else np.array([], dtype=int)
        )
        keep = np.concatenate([np.array(assigned_idx, dtype=int), extra])
    return sorted(int(i) for i in keep)


def _method_facts(config: dict[str, Any], backend: str) -> dict[str, Any]:
    """Every threshold and weight the page explains, read from the live config.

    The Method view states numbers — the Rotolo weights, the horizon
    cut-points, the critical-technology threshold. Hardcoding any of them in
    the page would let it drift into describing a pipeline that no longer
    exists, which is worse than not describing it at all.
    """
    topics_cfg = get(config, "emergence", "topics", default={}) or {}
    thresholds = get(topics_cfg, "similarity_thresholds", default={}) or {}
    method = str(topics_cfg.get("method", "agglomerative"))
    ct_thresholds = (
        get(config, "scoring", "strategic_fit", "critical_tech_match", "thresholds", default={})
        or {}
    )
    ct_threshold = ct_thresholds.get(backend)

    return {
        "time_slice": get(config, "emergence", "time_slice", default="year"),
        "clustering_method": method,
        "bertopic": get(topics_cfg, "bertopic", default={}) or {},
        "similarity_threshold": _round((thresholds.get(method) or {}).get(backend)),
        "attachment_threshold_ratio": _round(topics_cfg.get("attachment_threshold_ratio")),
        "min_topic_size": _int(topics_cfg.get("min_topic_size")),
        "max_topics": _int(topics_cfg.get("max_topics")),
        "min_docs_per_topic": _int(get(config, "emergence", "min_docs_per_topic", default=None)),
        "min_slices_for_growth": _int(
            get(config, "emergence", "min_slices_for_growth", default=None)
        ),
        "forming_sources": list(topics_cfg.get("forming_sources") or []),
        "burst": get(config, "emergence", "burst", default={}) or {},
        "rotolo_weights": get(config, "emergence", "rotolo_weights", default={}) or {},
        "three_horizons": get(config, "emergence", "three_horizons", default={}) or {},
        "weak_signal": get(config, "emergence", "weak_signal", default={}) or {},
        "strategic_fit": get(config, "scoring", "strategic_fit", default={}) or {},
        "asset_leverage": get(config, "scoring", "asset_leverage", default={}) or {},
        "critical_tech_threshold": _round(ct_threshold),
        # A blank cut-off is not a missing value to paper over — it is the
        # configured instruction to match nothing, and the page has to say so
        # rather than print an empty cell. See docs/method.md, Stage 3.
        "critical_tech_matching": ct_threshold is not None,
        "index_components": get(config, "opportunity_index", "components", default={}) or {},
        "index_min_documents": _int(
            get(config, "opportunity_index", "min_documents", default=None)
        ),
        "rank_weights": get(config, "synthesis", "rank_weights", default={}) or {},
        "shortlist_size": _int(get(config, "synthesis", "shortlist_size", default=15)),
        "evidence_documents_per_topic": _int(
            get(config, "synthesis", "evidence_documents_per_topic", default=8)
        ),
    }


def _collection_rows(conn: Any, run_id: str) -> list[dict[str, Any]]:
    """Per-source collection outcome, one row per source rather than per status.

    A source that succeeded on nine frames and failed on three is one story,
    not two rows a reader has to add up themselves.
    """
    by_source: dict[str, dict[str, Any]] = {}
    for row in db.collection_summary(conn, run_id):
        entry = by_source.setdefault(
            str(row["source"]),
            {"source": str(row["source"]), "records": 0, "queries": 0, "status": {}},
        )
        entry["records"] += int(row.get("records") or 0)
        entry["queries"] += int(row.get("queries") or 0)
        entry["status"][str(row["status"])] = int(row.get("queries") or 0)

    out = []
    for entry in by_source.values():
        status = entry["status"]
        # Worst outcome wins the summary badge: a source that partly failed
        # must not read as clean because most of its frames were fine.
        for level in ("failed", "skipped", "partial", "success"):
            if status.get(level):
                entry["worst"] = level
                break
        else:
            entry["worst"] = "success"
        out.append(entry)
    return sorted(out, key=lambda r: -r["records"])


def _corpus_profile(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts the Method view charts: by year, by source, by STEEPV, and crossed.

    Computed over the whole corpus, not the plotted sample, because Stage 1 is
    what this describes and downsampling happens later.
    """
    years = sorted({int(d["year"]) for d in documents if d.get("year")})
    sources = sorted({str(d["source"]) for d in documents if d.get("source")})
    steepv = sorted({str(d["steepv"]) for d in documents if d.get("steepv")})
    year_index = {y: i for i, y in enumerate(years)}
    source_index = {s: i for i, s in enumerate(sources)}

    grid = [[0] * len(sources) for _ in years]
    by_steepv: dict[str, int] = {s: 0 for s in steepv}
    by_source: dict[str, int] = {s: 0 for s in sources}
    for d in documents:
        if d.get("source"):
            by_source[str(d["source"])] += 1
        if d.get("steepv"):
            by_steepv[str(d["steepv"])] += 1
        if d.get("year") and d.get("source"):
            grid[year_index[int(d["year"])]][source_index[str(d["source"])]] += 1

    return {
        "years": years,
        "sources": sources,
        "steepv": steepv,
        "by_year_source": grid,
        "by_source": by_source,
        "by_steepv": by_steepv,
    }


def _strategy_rows(conn: Any) -> dict[str, list[dict[str, Any]]]:
    """The Stage 0 reference set, grouped by type, for the Method view.

    Truncated deliberately: the full text of fourteen objectives would double
    the page for something a reader can read in the YAML. What matters here is
    that the set is visible and countable.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ref in db.fetch_strategy_refs(conn):
        grouped.setdefault(str(ref["ref_type"]), []).append({
            "code": ref.get("code") or "",
            "label": ref.get("label") or ref["ref_id"],
            "weight": _round(ref.get("weight"), 2),
            "lexicon": [str(t) for t in (ref.get("lexicon") or [])][:12],
            "text": (str(ref.get("text") or "").strip())[:400],
        })
    return grouped


def _shorten(label: str, words: int = 3) -> str:
    """A map-sized version of a c-TF-IDF label.

    Labels are slash-joined term lists, which are informative in a table and
    unreadable printed over a point cloud.
    """
    parts = [p.strip() for p in str(label).split("/") if p.strip()]
    return " · ".join(parts[:words]) if parts else str(label)


def build_dashboard(config: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Assemble the JSON payload the dashboard page embeds."""
    conn = db.init_db(resolve_path(config, "storage", "duckdb_path"))
    try:
        documents = db.fetch_documents(conn)
        if not documents:
            raise SystemExit(f"No documents for run_id={run_id!r}. Run the pipeline first.")
        total_documents = len(documents)

        ranked_topics = db.fetch_ranked_topics(conn, run_id)
        if not ranked_topics:
            raise SystemExit(f"No ranked topics for run_id={run_id!r}. Run Stage 5 first.")

        membership = db.fetch_run_topic_documents(conn, run_id)
        collection = _collection_rows(conn, run_id)
        strategy = _strategy_rows(conn)
        corpus = _corpus_profile(documents)
        timeseries = {
            t["topic_id"]: db.fetch_topic_timeseries(conn, t["topic_id"]) for t in ranked_topics
        }

        # Same embedding recipe as Stage 2 (src/stage2_emergence.py): fit IDF
        # over the whole corpus, then encode with the cache. Deterministic —
        # a re-render of the same corpus under the same backend reproduces
        # the same vectors.
        texts = [document_text(d) for d in documents]
        embedder = build_embedder(config)
        embedder.fit(texts)
        vectors = encode_with_cache(
            embedder, texts, conn,
            enabled=bool(get(config, "embeddings", "cache_vectors", default=True)),
        )
    finally:
        conn.close()

    doc_topic: dict[str, tuple[str, float | None]] = {
        m["doc_id"]: (m["topic_id"], m.get("similarity")) for m in membership
    }

    max_points = int(get(config, "dashboard", "max_points", default=25000))
    keep = _select_points(documents, set(doc_topic), max_points, run_id)
    if len(keep) < len(documents):
        logger.info(
            "Dashboard: plotting %d of %d documents (dashboard.max_points=%d)",
            len(keep), len(documents), max_points,
        )
    documents = [documents[i] for i in keep]
    vectors = vectors[keep]

    coords, projection_method, projection_used = project_2d(vectors, config)

    repo_url = _repo_url(config)
    shortlist_size = int(get(config, "synthesis", "shortlist_size", default=15))
    topic_index = {t["topic_id"]: i for i, t in enumerate(ranked_topics)}

    sources = sorted({d["source"] for d in documents if d.get("source")})
    source_index = {s: i for i, s in enumerate(sources)}
    steepv_cats = sorted({d["steepv"] for d in documents if d.get("steepv")})
    steepv_index = {s: i for i, s in enumerate(steepv_cats)}

    xs, ys = [], []
    topic_col, sim_col, source_col, steepv_col = [], [], [], []
    year_col, citation_col, title_col, url_col, venue_col = [], [], [], [], []

    for doc, (x, y) in zip(documents, coords):
        xs.append(round(float(x), 4))
        ys.append(round(float(y), 4))
        tid, sim = doc_topic.get(doc["doc_id"], (None, None))
        topic_col.append(topic_index.get(tid, -1))
        sim_col.append(round(float(sim), 3) if sim is not None else None)
        source_col.append(source_index.get(doc.get("source"), -1))
        steepv_col.append(steepv_index.get(doc.get("steepv"), -1))
        year_col.append(doc.get("year"))
        citation_col.append(int(doc.get("citation_count") or 0))
        title_col.append((doc.get("title") or "(untitled)").strip()[:220])
        raw_url = str(doc.get("url") or "")
        url_col.append(raw_url if raw_url.startswith(("http://", "https://")) else "")
        venue_col.append((doc.get("venue") or "").strip()[:120])

    point_topics = np.asarray(topic_col, dtype=np.int32)
    fidelity = compute_fidelity(vectors, coords, point_topics, config)

    topics_out = []
    for i, t in enumerate(ranked_topics):
        member_idx = np.flatnonzero(point_topics == i)
        rank = t.get("rank")
        evidence_url = (
            f"{repo_url}/blob/main/data/outputs/{run_id}/evidence/"
            f"{int(rank):02d}_{t['topic_id']}.md"
            if rank and int(rank) <= shortlist_size else None
        )
        members = sorted(
            (
                {"i": int(j), "sim": sim_col[int(j)]}
                for j in member_idx
            ),
            key=lambda m: -(m["sim"] if m["sim"] is not None else 0.0),
        )
        series = timeseries.get(t["topic_id"], [])
        topics_out.append({
            "id": t["topic_id"],
            "label": t.get("label") or t["topic_id"],
            "short": _shorten(t.get("label") or t["topic_id"]),
            "terms": [str(term) for term, _weight in (t.get("terms") or [])][:12],
            "horizon": t.get("horizon"),
            "signal_class": t.get("signal_class"),
            "rank": rank,
            "document_count": t.get("document_count"),
            "plotted_count": int(member_idx.size),
            "emergence_score": _round(t.get("emergence_score")),
            "novelty": _round(t.get("novelty")),
            "growth": _round(t.get("growth")),
            "coherence": _round(t.get("coherence")),
            "impact": _round(t.get("impact")),
            "uncertainty": _round(t.get("uncertainty")),
            "burst_weight": _round(t.get("burst_weight"), 3),
            "burst_slices": [str(s) for s in (t.get("burst_slices") or [])],
            "strategic_fit": _round(t.get("strategic_fit")),
            "best_objective": t.get("best_objective"),
            "best_objective_sim": _round(t.get("best_objective_sim")),
            "asset_leverage": _round(t.get("asset_leverage")),
            "best_asset": t.get("best_asset"),
            "opportunity_index": (
                None if t.get("index_suppressed") else _round(t.get("opportunity_index"))
            ),
            "index_components": t.get("index_components") or {},
            "index_suppressed": bool(t.get("index_suppressed")),
            "composite_rank_score": _round(t.get("composite_rank_score")),
            "critical_tech": t.get("critical_tech"),
            "fit_quadrant": t.get("fit_quadrant") or "watch",
            "cagr": _round(t.get("cagr"), 4),
            "maturity": _round(t.get("maturity"), 3),
            "avg_proportion": _round(t.get("avg_proportion"), 5),
            "first_slice": t.get("first_slice"),
            "last_slice": t.get("last_slice"),
            "evidence_url": evidence_url,
            "docs": members[:40],
            "timeseries": [
                {
                    "slice": str(s["time_slice"]),
                    "n": int(s.get("doc_count") or 0),
                    "p": _round(s.get("proportion"), 5),
                    "burst": bool(s.get("in_burst")),
                }
                for s in series
            ],
            "hull": (
                topic_hull(coords[member_idx]) if member_idx.size >= 3 else []
            ),
            "cx": _round(float(np.median(coords[member_idx, 0])), 3) if member_idx.size else None,
            "cy": _round(float(np.median(coords[member_idx, 1])), 3) if member_idx.size else None,
            # How far the topic reaches on the map, so the page can put a label
            # clear of its points rather than over them. The 90th percentile
            # rather than the maximum, for the same reason topic_hull() trims.
            "spread": (
                _round(float(np.quantile(
                    np.linalg.norm(coords[member_idx] - np.median(coords[member_idx], axis=0), axis=1),
                    0.9,
                )), 3)
                if member_idx.size >= 3 else 0.0
            ),
            "map_purity": fidelity["topic_map_purity"].get(i),
            "space_purity": fidelity["topic_space_purity"].get(i),
        })

    years = [y for y in year_col if y]

    return {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "repo_url": repo_url,
        "backend": embedder.name,
        "dimensions": int(vectors.shape[1]),
        "projection_method": projection_method,
        "projection": {**projection_used, "resolved": projection_method},
        "fidelity": fidelity["summary"],
        "neighbours": fidelity["neighbours"],
        "shortlist_size": shortlist_size,
        "documents_total": total_documents,
        "documents_plotted": len(documents),
        "topics_total": len(ranked_topics),
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "sources": sources,
        "steepv": steepv_cats,
        "method": _method_facts(config, embedder.name),
        "collection": collection,
        "corpus": corpus,
        "strategy": strategy,
        "topics": topics_out,
        "points": {
            "x": xs, "y": ys,
            "topic": topic_col, "similarity": sim_col,
            "source": source_col, "steepv": steepv_col,
            "year": year_col, "citation": citation_col,
            "title": title_col, "url": url_col, "venue": venue_col,
        },
    }


def compute_fidelity(
    vectors: np.ndarray,
    coords: np.ndarray,
    point_topics: np.ndarray,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Measure how much the 2D map distorts the space the topics were found in.

    Returns the page-level summary, the per-topic purity pair, and the k
    nearest high-dimensional neighbours of every plotted point so the page can
    draw them. Degrades to an empty result rather than failing the build: a
    dashboard without a fidelity readout is worth having, and this is the
    expensive part of the module.
    """
    cfg = get(config, "dashboard", "fidelity", default={}) or {}
    empty = {
        "summary": {"computed": False, "reason": "dashboard.fidelity.enabled is false"},
        "topic_map_purity": {},
        "topic_space_purity": {},
        "neighbours": {"k": 0, "idx": []},
    }
    if not bool(cfg.get("enabled", True)):
        return empty

    n = int(vectors.shape[0])
    cap = int(cfg.get("max_points", 15000))
    if n > cap:
        logger.info(
            "Dashboard: %d points exceeds dashboard.fidelity.max_points=%d; "
            "skipping the projection fidelity measurement.", n, cap,
        )
        return {
            **empty,
            "summary": {
                "computed": False,
                "reason": f"{n:,} plotted points exceeds the {cap:,}-point budget",
            },
        }
    if n < 20:
        return {**empty, "summary": {"computed": False, "reason": "too few points to measure"}}

    k = max(2, min(int(cfg.get("k", 15)), n - 2))
    link_k = max(1, min(int(cfg.get("neighbour_links", 8)), n - 1))
    sample_size = max(50, min(int(cfg.get("sample", 2500)), n))

    space_nn = knn_indices(vectors, max(k, link_k), metric="cosine")
    map_nn = knn_indices(np.asarray(coords, dtype=np.float32), k, metric="euclidean")

    space_purity = neighbour_purity(space_nn[:, : k + 1], point_topics)
    map_purity = neighbour_purity(map_nn[:, : k + 1], point_topics)

    per_topic_map: dict[int, float] = {}
    per_topic_space: dict[int, float] = {}
    for topic in np.unique(point_topics):
        if topic < 0:
            continue
        members = point_topics == topic
        per_topic_map[int(topic)] = round(float(map_purity[members].mean()), 3)
        per_topic_space[int(topic)] = round(float(space_purity[members].mean()), 3)

    # Trustworthiness and continuity need the full rank matrix, which is
    # quadratic in memory, so they run on a seeded sample. Reported with the
    # sample size attached rather than presented as an exact figure.
    rng = np.random.default_rng(42)
    idx = (
        np.sort(rng.choice(n, size=sample_size, replace=False))
        if sample_size < n else np.arange(n)
    )
    trust, cont = trustworthiness_continuity(vectors[idx], np.asarray(coords)[idx], k)

    assigned = point_topics >= 0
    return {
        "summary": {
            "computed": True,
            "k": k,
            "sample": int(idx.size),
            "points": n,
            "trustworthiness": None if np.isnan(trust) else round(trust, 3),
            "continuity": None if np.isnan(cont) else round(cont, 3),
            "space_purity": (
                round(float(space_purity[assigned].mean()), 3) if assigned.any() else None
            ),
            "map_purity": (
                round(float(map_purity[assigned].mean()), 3) if assigned.any() else None
            ),
        },
        "topic_map_purity": per_topic_map,
        "topic_space_purity": per_topic_space,
        # Flattened, self excluded: the page reshapes it. A nested array of
        # arrays costs about 40% more bytes for the same numbers.
        "neighbours": {
            "k": link_k,
            "idx": space_nn[:, 1 : link_k + 1].astype(int).ravel().tolist(),
        },
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


def _asset(name: str) -> str:
    path = ASSET_DIR / name
    if not path.is_file():
        raise SystemExit(
            f"Missing dashboard asset {path}. The published page inlines these, "
            "so a checkout without src/dashboard_assets/ cannot build it."
        )
    return path.read_text(encoding="utf-8")


def _escape_for_script(payload: dict[str, Any]) -> str:
    """JSON-encode *payload* for embedding inside a <script> tag.

    Document titles and venues come from external APIs (Crossref, GDELT,
    arXiv) and are not trusted. A title containing a literal '</script>'
    would otherwise close the tag early and let arbitrary markup follow it
    on a page this pipeline publishes to the public internet. Escaping the
    one sequence that can do that is the standard, sufficient defence for
    JSON embedded this way.
    """
    return json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")


#: Stamps data-theme before first paint, so a reader whose stored choice or
#: system preference is dark never sees a white flash. The stylesheet defines
#: only one dark selector on the strength of this always running.
_THEME_SNIPPET = (
    "<script>(function(){var t=null;try{t=localStorage.getItem('bigthink-theme')}"
    "catch(e){}document.documentElement.setAttribute('data-theme',"
    "t==='dark'||t==='light'?t:(window.matchMedia&&"
    "window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'))})();</script>"
)


def render_html(data: dict[str, Any]) -> str:
    """One self-contained page: the payload, the stylesheet and every view."""
    parts: list[str] = [
        PAGE_HEAD,
        "<title>IPAVentures horizon scan — explorer</title>",
        f"<style>{_asset('dashboard.css')}</style>",
        _THEME_SNIPPET,
        "</head>",
        '<body>',
        _asset("shell.html"),
        f"<script>window.__DASHBOARD_DATA__ = {_escape_for_script(data)};</script>",
        "<script>\n(function () {\n\"use strict\";\n",
        "\n".join(_asset(name) for name in _JS_ASSETS),
        "\n})();\n</script>",
        PAGE_TAIL,
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(config: dict[str, Any], run_id: str) -> Path:
    docs_dir = REPO_ROOT / "docs"
    out = docs_dir / "dashboard.html"
    if not bool(get(config, "dashboard", "enabled", default=True)):
        logger.info("dashboard.enabled is false; leaving %s untouched.", out)
        return out

    data = build_dashboard(config, run_id)
    docs_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(data), encoding="utf-8")
    # GitHub Pages runs Jekyll by default, which skips files and directories
    # beginning with an underscore. Nothing here needs Jekyll.
    (docs_dir / ".nojekyll").write_text("", encoding="utf-8")

    fidelity = data["fidelity"]
    logger.info(
        "Wrote %s (%d of %d documents, %d topics, projection=%s%s)",
        out, data["documents_plotted"], data["documents_total"],
        data["topics_total"], data["projection_method"],
        (
            f", trustworthiness={fidelity['trustworthiness']}"
            f", continuity={fidelity['continuity']}"
            if fidelity.get("computed") else ", fidelity not measured"
        ),
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the interactive dashboard.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)-7s %(message)s")
    path = run(load_config(args.config), args.run_id)
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
