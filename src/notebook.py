"""
src/notebook.py — export a run as a peer-reviewable Jupyter notebook.

WHY THIS EXISTS. The HTML report and the shortlist say *what* the pipeline
concluded. Neither shows a reviewer *how*, and "trust the ranking" is not a
position this method can defend — no weight in it has been validated (see
PROJECT_STATE.md, open issue 1). This module produces the artefact a colleague
can actually argue with: every stage's inputs, the transformation applied, the
numbers that came out, and the arithmetic re-derived in front of them.

WHAT IT IS NOT. It does not re-run the pipeline. It reads one frozen run out of
DuckDB and explains it. That distinction is the whole design:

  * Stages 2-5 are deterministic functions of the corpus and the config, so
    re-deriving them in a notebook is meaningful — the same inputs must give
    the same numbers, and the notebook proves it rather than asserting it.
  * Stage 1 is not. Collectors hit live APIs under rate limits, so "run this
    cell to reproduce the corpus" would be a lie: a reviewer running it in
    six weeks gets a different corpus and different topics. The notebook
    therefore *describes* collection from `collection_log` and re-derives only
    what is genuinely re-derivable.

THE VERIFICATION CELLS ARE THE POINT. Four numbers get recomputed from stored
inputs and checked against what the pipeline stored:

    emergence_score       from the five Rotolo attributes + rotolo_weights
    horizon               from fitted maturity + the Three Horizons cut-points
    opportunity_index     from stored components + effective weights
    composite_rank_score  from the three ranking axes + rank_weights, using
                          stage5_synthesis.composite_scores itself

The last one matters most: it calls the production function, not a
reimplementation of it, so the check cannot drift away from the code it is
supposed to be checking. A reviewer who accepts the corpus and the weights has
no remaining room to doubt the ordering — which usefully moves the argument to
where it belongs, onto the corpus and the weights.

WEIGHTS COME FROM THE RUN, NOT FROM DISK. Verification loads the config from
`pipeline_runs.config_snapshot`, not from today's bigthink_config.yaml. A
threshold edited after the run must not silently change what "reproduced"
means; where the two disagree the notebook says so explicitly.

EXECUTION. Cells are executed at generation time, in order, in one shared
namespace, and their real stdout is embedded — so the notebook reads correctly
without being run, while every cell in it remains runnable. Execution is
in-process rather than through nbclient/jupyter so that this stays a
standard-library-plus-DuckDB job, like the rest of the repo; a `.ipynb` is only
JSON, and `nbformat` would buy nothing but a dependency.

Run:
    python -m src.notebook --run-id 2026-08-29
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import duckdb
import numpy as np
import yaml

from src.config import REPO_ROOT, get, load_config, resolve_path

logger = logging.getLogger(__name__)

NBFORMAT = 4
NBFORMAT_MINOR = 5

# A single cell that prints more than this is truncated. Nothing here is meant
# to print at that scale; the cap exists so a pathological run cannot produce a
# 40 MB notebook that no reviewer can open.
MAX_OUTPUT_CHARS = 60_000

# Recomputation tolerances. Emergence and the composite rank are pure float
# arithmetic over values stored at full precision, so they must agree to
# machine epsilon. The opportunity index cannot: Stage 4 stores its components
# and effective weights rounded to 4 dp for legibility, while the index it
# stored was computed from the unrounded values. That rounding is the entire
# error budget, and it is worth stating out loud rather than hiding behind a
# loose tolerance everywhere.
TOLERANCE_EXACT = 1e-9
TOLERANCE_INDEX = 1e-3

_ROTOLO_ATTRIBUTES = ("novelty", "growth", "coherence", "impact", "uncertainty")


# ---------------------------------------------------------------------------
# Helpers used *inside* the generated notebook
#
# These are imported by the notebook rather than pasted into it, so that what a
# reviewer runs is the same tested code the exporter runs. They print as well
# as return: in a notebook the printed line is the evidence.
# ---------------------------------------------------------------------------


def verify_close(
    name: str,
    stored: Sequence[float],
    recomputed: Sequence[float],
    *,
    tolerance: float = TOLERANCE_EXACT,
) -> bool:
    """Check a recomputed vector against what the pipeline stored.

    Reports the largest absolute deviation rather than a bare pass/fail, so the
    output carries its own evidence — "agrees to 1e-16" and "agrees to 9e-4"
    are different claims and a reviewer should be able to tell them apart.
    """
    expected = np.asarray(list(stored), dtype=np.float64)
    actual = np.asarray(list(recomputed), dtype=np.float64)

    if expected.shape != actual.shape:
        print(
            f"FAIL  {name}: recomputed {actual.shape[0]} values against "
            f"{expected.shape[0]} stored — the two sets do not describe the same topics."
        )
        return False
    if expected.size == 0:
        print(f"SKIP  {name}: nothing to check (no topics in this population).")
        return True

    deviation = float(np.max(np.abs(expected - actual)))
    ok = deviation <= tolerance
    print(
        f"{'PASS' if ok else 'FAIL'}  {name} reproduced for {expected.size} topics "
        f"— largest deviation {deviation:.3g} (tolerance {tolerance:g})"
    )
    if not ok:
        worst = int(np.argmax(np.abs(expected - actual)))
        print(f"      worst at position {worst}: stored {expected[worst]!r}, "
              f"recomputed {actual[worst]!r}")
    return ok


def verify_identical(name: str, stored: Sequence[Any], recomputed: Sequence[Any]) -> bool:
    """Check a recomputed sequence of labels or ids element by element.

    Used for the ranking order and the Three Horizons band. Equality of scores
    is not the same claim as equality of *ordering*, and the ordering is what
    anyone actually reads off the shortlist.
    """
    expected, actual = list(stored), list(recomputed)
    if len(expected) != len(actual):
        print(
            f"FAIL  {name}: recomputed {len(actual)} entries against {len(expected)} stored."
        )
        return False
    if not expected:
        print(f"SKIP  {name}: nothing to check.")
        return True

    matches = sum(1 for a, b in zip(expected, actual) if a == b)
    ok = matches == len(expected)
    print(
        f"{'PASS' if ok else 'FAIL'}  {name} reproduced — {matches} of {len(expected)} "
        f"entries identical"
    )
    if not ok:
        first = next(i for i, (a, b) in enumerate(zip(expected, actual)) if a != b)
        print(f"      first divergence at position {first}: "
              f"stored {expected[first]!r}, recomputed {actual[first]!r}")
    return ok


def table(
    rows: Sequence[Sequence[Any]],
    headers: Sequence[str],
    *,
    max_width: int = 52,
    places: int = 3,
) -> str:
    """Render rows as a fixed-width text table.

    Plain text rather than pandas or HTML: it needs no dependency the pipeline
    does not already have, it renders identically everywhere including in a
    diff, and a notebook that has been committed to the repository should
    produce readable diffs when the numbers move.
    """
    def cell(value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, float):
            return f"{value:.{places}f}"
        text = str(value).replace("\n", " ")
        return text if len(text) <= max_width else text[: max_width - 1] + "…"

    body = [[cell(v) for v in row] for row in rows]
    numeric = [
        all(
            isinstance(row[i], (int, float)) and not isinstance(row[i], bool)
            for row in rows if row[i] is not None
        )
        and any(row[i] is not None for row in rows)
        for i in range(len(headers))
    ]
    widths = [
        max(len(str(headers[i])), *(len(r[i]) for r in body)) if body else len(str(headers[i]))
        for i in range(len(headers))
    ]

    def line(values: Sequence[str]) -> str:
        return "  ".join(
            v.rjust(widths[i]) if numeric[i] else v.ljust(widths[i])
            for i, v in enumerate(values)
        ).rstrip()

    out = [line([str(h) for h in headers]), "  ".join("-" * w for w in widths)]
    out.extend(line(r) for r in body)
    if not body:
        out.append("(no rows)")
    return "\n".join(out)


def diff_config(stored: Any, current: Any, prefix: str = "") -> list[tuple[str, Any, Any]]:
    """Leaf-level differences between two config trees, as (path, stored, current).

    Used to tell a reviewer whether the repository they are reading still
    matches the run they are reading about. Comparing scores across configs is
    the mistake this whole project is most exposed to.
    """
    if isinstance(stored, dict) and isinstance(current, dict):
        out: list[tuple[str, Any, Any]] = []
        for key in sorted(set(stored) | set(current)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in stored:
                out.append((path, None, current[key]))
            elif key not in current:
                out.append((path, stored[key], None))
            else:
                out.extend(diff_config(stored[key], current[key], path))
        return out
    return [] if stored == current else [(prefix, stored, current)]


# ---------------------------------------------------------------------------
# Notebook construction
# ---------------------------------------------------------------------------


def _lines(text: str) -> list[str]:
    """Split into the line list an .ipynb stores, keeping newlines."""
    return text.splitlines(keepends=True)


class NotebookBuilder:
    """Accumulates cells, executing code cells so their real output is embedded.

    Cells share one namespace, exactly as they would in a kernel, so the
    notebook reads top to bottom and a reviewer re-running it gets the same
    sequence. A cell that raises does not abort the export: the traceback is
    embedded as an error output where the reviewer can see it, and the failure
    is reported to the caller. A notebook that shows where it broke is more
    useful than no notebook, and silently omitting the broken part would be
    exactly the kind of quiet failure this repository tries not to have.
    """

    def __init__(self, *, execute: bool = True, workdir: Path | None = None) -> None:
        self.cells: list[dict[str, Any]] = []
        self.namespace: dict[str, Any] = {"__name__": "__bigthink_notebook__"}
        self.execute = execute
        self.workdir = workdir or REPO_ROOT
        self.execution_count = 0
        self.failures: list[str] = []

    # -- cell types --------------------------------------------------------

    def markdown(self, text: str) -> None:
        self.cells.append(
            {
                "cell_type": "markdown",
                "id": self._next_id(),
                "metadata": {},
                "source": _lines(text.strip("\n")),
            }
        )

    def code(self, source: str) -> None:
        source = source.strip("\n")
        cell: dict[str, Any] = {
            "cell_type": "code",
            "id": self._next_id(),
            "metadata": {},
            "execution_count": None,
            "outputs": [],
            "source": _lines(source),
        }
        self.cells.append(cell)
        if self.execute:
            self.execution_count += 1
            cell["execution_count"] = self.execution_count
            cell["outputs"] = self._run(source)

    # -- execution ---------------------------------------------------------

    def _run(self, source: str) -> list[dict[str, Any]]:
        buffer = io.StringIO()
        # Cells resolve the repository by walking up from the working
        # directory, which is how a reviewer's kernel will find it too. Running
        # them from the repo root at generation time exercises that same path.
        try:
            with contextlib.chdir(self.workdir), contextlib.redirect_stdout(buffer):
                exec(compile(source, "<notebook-cell>", "exec"), self.namespace)  # noqa: S102
        except BaseException as exc:  # noqa: BLE001 - recorded, not swallowed
            text = self._truncate(buffer.getvalue())
            outputs = [self._stream(text)] if text else []
            self.failures.append(f"{type(exc).__name__}: {exc}")
            outputs.append(
                {
                    "output_type": "error",
                    "ename": type(exc).__name__,
                    "evalue": str(exc),
                    "traceback": traceback.format_exc().splitlines(),
                }
            )
            logger.warning("Notebook cell %d failed: %s: %s",
                           self.execution_count, type(exc).__name__, exc)
            return outputs

        text = self._truncate(buffer.getvalue())
        return [self._stream(text)] if text else []

    @staticmethod
    def _stream(text: str) -> dict[str, Any]:
        return {"output_type": "stream", "name": "stdout", "text": _lines(text)}

    @staticmethod
    def _truncate(text: str) -> str:
        if len(text) <= MAX_OUTPUT_CHARS:
            return text
        return (
            text[:MAX_OUTPUT_CHARS]
            + f"\n… output truncated at {MAX_OUTPUT_CHARS:,} characters …\n"
        )

    # -- assembly ----------------------------------------------------------

    def _next_id(self) -> str:
        # Deterministic ids, so regenerating the same run produces a file that
        # diffs cleanly against the previous one instead of churning every cell.
        return f"cell-{len(self.cells):04d}"

    def notebook(self, title: str) -> dict[str, Any]:
        return {
            "cells": self.cells,
            "metadata": {
                "kernelspec": {
                    "display_name": "Python 3",
                    "language": "python",
                    "name": "python3",
                },
                "language_info": {
                    "name": "python",
                    "version": ".".join(str(v) for v in sys.version_info[:3]),
                    "mimetype": "text/x-python",
                    "file_extension": ".py",
                    "pygments_lexer": "ipython3",
                },
                "title": title,
            },
            "nbformat": NBFORMAT,
            "nbformat_minor": NBFORMAT_MINOR,
        }


# ---------------------------------------------------------------------------
# Analyst observations
# ---------------------------------------------------------------------------

# Anchors an observations file may attach commentary to. Per-topic notes use
# the key "topic:<topic_id>".
OBSERVATION_ANCHORS = (
    "overview", "provenance", "stage0", "stage1", "stage2",
    "stage3", "stage4", "stage5", "evidence", "closing",
)


def load_observations(path: Path) -> dict[str, str]:
    """Load analyst commentary keyed by anchor.

    The pipeline can describe what it did; it cannot tell you which topics are
    clustering artefacts, and reading the evidence cards is the cheapest
    quality control in the method. This is where that reading gets written
    down, so that the judgement travels with the numbers instead of living in
    someone's head or in a separate email.
    """
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{path} must be a YAML mapping of anchor -> markdown text; got {type(raw).__name__}."
        )

    observations: dict[str, str] = {}
    for key, value in raw.items():
        key = str(key)
        if key not in OBSERVATION_ANCHORS and not key.startswith("topic:"):
            raise ValueError(
                f"{path}: unknown observation anchor {key!r}. Valid anchors are "
                f"{list(OBSERVATION_ANCHORS)} or 'topic:<topic_id>'."
            )
        if value is not None and str(value).strip():
            observations[key] = str(value).strip()
    return observations


def auto_observations(
    conn: duckdb.DuckDBPyConnection, config: dict[str, Any], run_id: str
) -> dict[str, list[str]]:
    """Facts about this run worth flagging, derived from the run's own numbers.

    Deliberately restricted to things that are *checkable from the data shown
    in the cell above them*. This is not interpretation — interpretation is the
    analyst's job and goes in the observations file. It is the set of caveats
    that are easy to miss and expensive to miss.
    """
    notes: dict[str, list[str]] = {anchor: [] for anchor in OBSERVATION_ANCHORS}

    # --- collection health ------------------------------------------------
    enabled = {
        name for name, block in (get(config, "collection", "sources", default={}) or {}).items()
        if isinstance(block, dict) and block.get("enabled")
    }
    contributing = {
        row[0]: int(row[1])
        for row in conn.execute(
            "SELECT source, count(*) FROM documents GROUP BY source"
        ).fetchall()
    }
    silent = sorted(enabled - set(contributing))
    if silent:
        notes["stage1"].append(
            f"**{', '.join(silent)}** {'is' if len(silent) == 1 else 'are'} enabled in the "
            f"config but contributed no documents. A source that is configured and silent "
            f"is not the same as a source that was never asked — check `collection_log` "
            f"above for whether it failed or simply matched nothing."
        )
    failures = conn.execute(
        "SELECT source, count(*) FROM collection_log WHERE run_id = ? AND status = 'failed' "
        "GROUP BY source ORDER BY 2 DESC",
        [run_id],
    ).fetchall()
    if failures:
        detail = ", ".join(f"{src} ({n})" for src, n in failures)
        notes["stage1"].append(
            f"Failed queries by source: {detail}. Each failure costs that frame's "
            f"contribution to the corpus, so the shortlist is drawn from a slightly "
            f"different scan than the frame file describes."
        )

    # --- how much of the corpus actually became evidence ------------------
    total_docs = int(conn.execute("SELECT count(*) FROM documents").fetchone()[0] or 0)
    assigned = int(
        conn.execute(
            "SELECT count(DISTINCT doc_id) FROM topic_documents WHERE topic_id IN "
            "(SELECT topic_id FROM topics WHERE run_id = ?)",
            [run_id],
        ).fetchone()[0]
        or 0
    )
    if total_docs:
        share = assigned / total_docs
        notes["stage2"].append(
            f"{assigned:,} of {total_docs:,} documents ({share:.0%}) are attached to any "
            f"topic. The remainder did not cluster tightly enough to be assigned and are "
            f"absent from every score below."
            + (
                " At this coverage the shortlist describes a minority of what was "
                "collected — worth stating in any briefing that quotes it."
                if share < 0.5 else ""
            )
        )

    # --- axis compression -------------------------------------------------
    axis_rows = conn.execute(
        "SELECT min(strategic_fit), max(strategic_fit), min(asset_leverage), "
        "max(asset_leverage) FROM topic_scores WHERE run_id = ?",
        [run_id],
    ).fetchone()
    if axis_rows and axis_rows[0] is not None:
        fit_lo, fit_hi, lev_lo, lev_hi = (float(v or 0.0) for v in axis_rows)
        for label, lo, hi, weight_key in (
            ("Strategic fit", fit_lo, fit_hi, "strategic_fit"),
            ("Asset leverage", lev_lo, lev_hi, "asset_leverage"),
        ):
            if hi - lo < 0.15:
                weight = float(
                    get(config, "synthesis", "rank_weights", weight_key, default=0.0)
                )
                notes["stage3"].append(
                    f"**{label} spans only {lo:.2f}–{hi:.2f}.** Rank-normalisation still "
                    f"gives it its configured {weight:.0%} of the ordering, so a "
                    f"compressed axis is not a weak axis in the ranking — but the "
                    f"underlying signal separating adjacent topics on it is thin, and "
                    f"small differences here should not be read as meaningful."
                )

    # --- opportunity index ------------------------------------------------
    suppressed = int(
        conn.execute(
            "SELECT count(*) FROM topic_scores WHERE run_id = ? AND index_suppressed",
            [run_id],
        ).fetchone()[0]
        or 0
    )
    scored_total = int(
        conn.execute(
            "SELECT count(*) FROM topic_scores WHERE run_id = ?", [run_id]
        ).fetchone()[0]
        or 0
    )
    if suppressed:
        minimum = int(get(config, "opportunity_index", "min_documents", default=15))
        notes["stage4"].append(
            f"{suppressed} of {scored_total} topics carry fewer than {minimum} documents "
            f"and have no index at all. They are suppressed rather than scored zero: an "
            f"unmeasurable index and a low one are different claims."
        )
    omitted_row = conn.execute(
        "SELECT index_components FROM topic_scores WHERE run_id = ? AND index_components IS NOT NULL "
        "LIMIT 1",
        [run_id],
    ).fetchone()
    if omitted_row:
        omitted = json.loads(omitted_row[0]).get("_omitted_components") or []
        if omitted:
            notes["stage4"].append(
                f"Component(s) **{', '.join(omitted)}** had no data and their weight was "
                f"redistributed across the rest. The index remains internally consistent "
                f"but measures fewer things than the config describes — say so when "
                f"presenting it, rather than quoting the configured weights."
            )

    # --- horizon banding sensitivity --------------------------------------
    h1 = float(get(config, "emergence", "three_horizons", "h1_max_maturity", default=0.75))
    h2 = float(get(config, "emergence", "three_horizons", "h2_max_maturity", default=0.35))
    borderline = conn.execute(
        "SELECT label, maturity, horizon FROM topics WHERE run_id = ? AND ("
        "abs(maturity - ?) < 0.05 OR abs(maturity - ?) < 0.05) ORDER BY maturity",
        [run_id, h1, h2],
    ).fetchall()
    if borderline:
        listed = "; ".join(f"{lab[:40]} ({mat:.2f} → {hz})" for lab, mat, hz in borderline)
        notes["stage2"].append(
            f"{len(borderline)} topic(s) sit within 0.05 of a Three Horizons cut-point "
            f"({listed}). Their band is an artefact of where the cut falls, not a finding "
            f"about them; do not present these as confidently H1/H2/H3."
        )

    return notes


# ---------------------------------------------------------------------------
# Cell text
#
# Kept as module-level templates so the code a reviewer runs is visible here in
# the repository too, rather than assembled out of fragments.
# ---------------------------------------------------------------------------

_SETUP = '''
import json
import sys
from pathlib import Path

import duckdb
import numpy as np

# Find the repository, so this notebook runs from wherever it has been opened.
REPO_ROOT = Path.cwd().resolve()
while not (REPO_ROOT / "bigthink_config.yaml").exists() and REPO_ROOT != REPO_ROOT.parent:
    REPO_ROOT = REPO_ROOT.parent
if not (REPO_ROOT / "bigthink_config.yaml").exists():
    raise SystemExit(
        "Could not locate the BigThink repository from the current directory. "
        "Open this notebook from inside a checkout of the repo."
    )
sys.path.insert(0, str(REPO_ROOT))

from src import db
from src.notebook import diff_config, table, verify_close, verify_identical

RUN_ID = {run_id!r}
DB_PATH = REPO_ROOT / {db_path!r}

# Read-only, deliberately. This notebook exists to explain a run; it must not
# be able to alter the corpus that run was computed from.
conn = duckdb.connect(str(DB_PATH), read_only=True)

print(f"run_id    {{RUN_ID}}")
print(f"database  {{DB_PATH}}")
print(f"documents {{conn.execute('SELECT count(*) FROM documents').fetchone()[0]:,}}")
'''

_PROVENANCE = '''
# The weights checked throughout this notebook come from the run itself, not
# from today's bigthink_config.yaml. Editing a threshold after a run must not
# quietly change what "reproduced" means.
snapshot = conn.execute(
    """
    SELECT stage, config_snapshot FROM pipeline_runs
    WHERE run_id = ? AND config_snapshot IS NOT NULL AND config_snapshot <> ''
    ORDER BY id DESC LIMIT 1
    """,
    [RUN_ID],
).fetchone()
if not snapshot:
    raise SystemExit(f"No config snapshot recorded for run_id={RUN_ID!r}.")

CONFIG = json.loads(snapshot[1])
ROTOLO_WEIGHTS = CONFIG["emergence"]["rotolo_weights"]
RANK_WEIGHTS = CONFIG["synthesis"]["rank_weights"]

print(f"Config recovered from the {snapshot[0]!r} stage log.\\n")
print("Embedding backend :", CONFIG["embeddings"]["backend"])
_topics_cfg = CONFIG["emergence"]["topics"]
_backend = CONFIG["embeddings"]["backend"]
_method = _topics_cfg.get("method", "agglomerative")
# Runs before 2026-08-30 stored one threshold map keyed by backend alone, when
# there was only one numpy method. Read either shape, so an old notebook still
# reports the number its own run actually used.
_thresh = (_topics_cfg.get("similarity_thresholds", {}).get(_method)
           or _topics_cfg.get("similarity_threshold_by_backend", {}))
print("Clustering method :", _method)
print("Clustering thresh :", _thresh.get(_backend, "not recorded"))
print("Time slice        :", CONFIG["emergence"]["time_slice"])
print("Collection window :",
      CONFIG["collection"]["start_year"], "-", CONFIG["collection"]["end_year"])
print()
print("Rotolo weights (emergence):",
      ", ".join(f"{k} {v}" for k, v in sorted(ROTOLO_WEIGHTS.items())))
print("Rank weights   (shortlist):",
      ", ".join(f"{k} {v}" for k, v in sorted(RANK_WEIGHTS.items())))
'''

_STAGE_LOG = '''
print(table(
    conn.execute(
        """
        SELECT stage, status, records_in, records_out,
               round(date_diff('millisecond', started_at, finished_at) / 1000.0, 1) AS seconds,
               message
        FROM pipeline_runs WHERE run_id = ? ORDER BY id
        """,
        [RUN_ID],
    ).fetchall(),
    ["stage", "status", "in", "out", "seconds", "message"],
    max_width=64,
))
'''

_CONFIG_DRIFT = '''
from src.config import load_config, snapshot_config

# Has the repository moved since this run? Comparing scores across configs is
# the mistake this method is most exposed to, so the notebook checks rather
# than assuming.
current = json.loads(snapshot_config(load_config()))
differences = diff_config(CONFIG, current)
if not differences:
    print("The config on disk is identical to the one that produced this run.")
else:
    print(f"{len(differences)} config value(s) have changed since this run.")
    print("Scores below were computed under the STORED values, not these.\\n")
    print(table(
        [(p, w, n) for p, w, n in differences[:30]],
        ["setting", "at run time", "on disk now"],
        max_width=44,
    ))
'''

_STAGE0 = '''
print(table(
    conn.execute(
        """
        SELECT ref_type, count(*) AS refs, round(avg(weight), 2) AS avg_weight
        FROM strategy_refs GROUP BY ref_type ORDER BY ref_type
        """
    ).fetchall(),
    ["reference type", "count", "avg weight"],
))
print()
print("Every strategic-fit score below is a similarity against one of these.")
print("A topic the strategy does not describe cannot score highly on fit, however")
print("important it is — that is a property of the instrument, not a finding.\\n")
print(table(
    conn.execute(
        "SELECT ref_type, code, label FROM strategy_refs ORDER BY ref_type, code, ref_id"
    ).fetchall(),
    ["type", "code", "label"],
    max_width=58,
))
'''

_STAGE1_SOURCES = '''
# NOTE the difference between these two tables. The corpus ACCUMULATES across
# runs — each run adds to it and growth curves lengthen instead of resetting —
# so the first table is the whole corpus the topics were formed from. The
# second is only what THIS run fetched. They are not meant to reconcile.
print("The corpus these topics were formed from, by source:\\n")
print(table(
    conn.execute(
        """
        SELECT source, count(*) AS documents, min(year) AS first_year,
               max(year) AS last_year, count(DISTINCT scan_frame_key) AS frames
        FROM documents GROUP BY source ORDER BY documents DESC
        """
    ).fetchall(),
    ["source", "documents", "first year", "last year", "frames"],
))
print()
print("What this run's collection actually did (per source query):\\n")
print(table(
    conn.execute(
        """
        SELECT source, status, count(*) AS queries, sum(records) AS records
        FROM collection_log WHERE run_id = ?
        GROUP BY source, status ORDER BY source, status
        """,
        [RUN_ID],
    ).fetchall(),
    ["source", "status", "queries", "records"],
))
'''

_STAGE1_FRAMES = '''
# The scan frame is the single biggest determinant of what this scan could
# possibly have found, so it is worth seeing which seed queries actually
# yielded a corpus and which returned almost nothing. A frame near the bottom
# of this table is a blind spot, whatever the shortlist says.
print(table(
    conn.execute(
        """
        SELECT scan_frame_key, any_value(steepv) AS steepv, count(*) AS documents,
               count(DISTINCT source) AS sources
        FROM documents WHERE scan_frame_key IS NOT NULL
        GROUP BY scan_frame_key ORDER BY documents DESC
        """
    ).fetchall(),
    ["scan frame", "STEEPV", "documents", "sources"],
    max_width=40,
))
'''

_STAGE1_SHAPE = '''
print("Documents per year — the history the growth curves are fitted to:\\n")
print(table(
    conn.execute(
        "SELECT year, count(*) FROM documents WHERE year IS NOT NULL "
        "GROUP BY year ORDER BY year"
    ).fetchall(),
    ["year", "documents"],
))
print()
print("STEEPV coverage. The scan frame decides this distribution, and it")
print("mirrors where free structured data exists rather than where")
print("opportunities are:\\n")
print(table(
    conn.execute(
        "SELECT steepv, count(*) FROM documents GROUP BY steepv ORDER BY 2 DESC"
    ).fetchall(),
    ["STEEPV", "documents"],
))
'''

_STAGE2_TOPICS = '''
topics = db.fetch_topics(conn, RUN_ID)   # emergence_score DESC — Stage 5 read them in this order

print(table(
    [
        (t["topic_id"], (t["label"] or "")[:44], t["document_count"],
         t["novelty"], t["growth"], t["coherence"], t["impact"], t["uncertainty"],
         t["emergence_score"], t["horizon"], t["signal_class"])
        for t in topics
    ],
    ["topic", "label", "docs", "nov", "grw", "coh", "imp", "unc",
     "emergence", "H", "signal"],
    places=2,
))
'''

_STAGE2_VERIFY = '''
from src.normalise import percentile_rank
from src.stage2_emergence import assign_horizon

ATTRS = ("novelty", "growth", "coherence", "impact", "uncertainty")

# emergence_score is a weighted sum of the five attributes AFTER each has been
# percentile-ranked within this run's population. Rank-normalising is what
# makes the configured weights mean what they say: a weighted sum of raw values
# is dominated by whichever attribute happens to have the widest spread.
#
# The consequence, stated plainly: the score is RELATIVE TO THIS RUN. A run of
# uniformly dull topics still produces one scoring near 1.0.
ranked = {a: percentile_rank([float(t[a]) for t in topics]) for a in ATTRS}
recomputed = [
    sum(ranked[a][i] * float(ROTOLO_WEIGHTS[a]) for a in ATTRS)
    for i in range(len(topics))
]
verify_close("emergence_score", [t["emergence_score"] for t in topics], recomputed)

# The Three Horizons band is a pure function of the fitted logistic maturity
# and two cut-points from the config. Nothing about a topic's age enters it.
verify_identical(
    "Three Horizons band",
    [t["horizon"] for t in topics],
    [assign_horizon(float(t["maturity"]), CONFIG) for t in topics],
)
'''

_STAGE3 = '''
ranked_topics = db.fetch_ranked_topics(conn, RUN_ID)

print(table(
    [
        (r["topic_id"], (r["label"] or "")[:38], r["strategic_fit"],
         r["best_objective"] or "—", r["asset_leverage"],
         r["critical_tech"] or "—")
        for r in ranked_topics
    ],
    ["topic", "label", "fit", "closest objective", "leverage", "DISR field"],
    max_width=36,
))
print()
fits = [float(r["strategic_fit"] or 0.0) for r in ranked_topics]
levs = [float(r["asset_leverage"] or 0.0) for r in ranked_topics]
print(f"strategic fit  range {min(fits):.3f}–{max(fits):.3f}  median {np.median(fits):.3f}")
print(f"asset leverage range {min(levs):.3f}–{max(levs):.3f}  median {np.median(levs):.3f}")
'''

_STAGE4 = '''
scored = [r for r in ranked_topics if not r["index_suppressed"]]
suppressed = [r for r in ranked_topics if r["index_suppressed"]]

if scored:
    weights = scored[0]["index_components"].get("_effective_weights", {})
    print("Effective component weights after redistribution:")
    print("  " + ", ".join(f"{k} {v}" for k, v in sorted(weights.items())))
    omitted = scored[0]["index_components"].get("_omitted_components") or []
    print("  omitted for lack of data: " + (", ".join(omitted) if omitted else "none"))
    print()

    keys = sorted(k for k in scored[0]["index_components"] if not k.startswith("_"))
    print(table(
        [
            (r["topic_id"], (r["label"] or "")[:36],
             *[r["index_components"].get(k) for k in keys], r["opportunity_index"])
            for r in scored
        ],
        ["topic", "label", *keys, "index"],
        max_width=36,
    ))

if suppressed:
    print()
    print(f"{len(suppressed)} topic(s) suppressed as too thin to index honestly: "
          + ", ".join(r["topic_id"] for r in suppressed))
'''

_STAGE4_VERIFY = '''
from src.notebook import TOLERANCE_INDEX

# The index is a plain weighted sum of stored components, so it is checkable
# the same way everything else here is. It is the one check that cannot run to
# machine precision: Stage 4 stores its components and effective weights
# rounded to 4 dp for legibility, while the index it stored was computed from
# the unrounded values. That rounding is the entire error budget below, which
# is why the tolerance is 1e-3 and not 1e-9. A deviation materially larger than
# ~1e-4 here is not rounding and should be treated as a defect.
verify_close(
    "opportunity_index",
    [r["opportunity_index"] for r in scored],
    [
        sum(r["index_components"][k] * w
            for k, w in r["index_components"]["_effective_weights"].items())
        for r in scored
    ],
    tolerance=TOLERANCE_INDEX,
)
'''

_STAGE5_VERIFY = '''
from src.stage5_synthesis import composite_scores

# Reproduced by calling the pipeline's own ranking function, not by
# reimplementing it here. A reimplementation can drift away from the code it is
# meant to be checking; this cannot.
#
# Stage 5 read topics in emergence_score DESC order and sorted them stably, so
# the reproduction has to start from that same order to be faithful.
by_id = {r["topic_id"]: r for r in ranked_topics}
rows = [by_id[t["topic_id"]] for t in topics]

recomputed = composite_scores(rows, RANK_WEIGHTS)
verify_close("composite_rank_score", [r["composite_rank_score"] for r in rows], recomputed)

order = sorted(range(len(rows)), key=lambda i: -recomputed[i])
verify_identical(
    "shortlist ordering",
    [r["topic_id"] for r in ranked_topics],        # stored, ORDER BY rank
    [rows[i]["topic_id"] for i in order],          # recomputed
)
'''

_STAGE5_TABLE = '''
print(table(
    [
        (r["rank"], (r["label"] or r["topic_id"])[:42], r["horizon"], r["signal_class"],
         r["emergence_score"], r["strategic_fit"], r["asset_leverage"],
         None if r["index_suppressed"] else r["opportunity_index"],
         r["composite_rank_score"])
        for r in ranked_topics
    ],
    ["#", "topic", "H", "signal", "emrg", "fit", "lev", "index", "composite"],
    places=3, max_width=42,
))
'''

_QUADRANTS = '''
from src.stage5_synthesis import quadrant

# Recomputed here rather than read back: Stage 5 records the quadrant in
# topics.csv but does not persist it to `topic_scores`, so the database cannot
# be asked for it. Recomputing from the stored axes and the same median split
# Stage 5 used gives the identical placement, and shows the derivation.
fit_cut = float(np.median([float(r["strategic_fit"] or 0.0) for r in ranked_topics]))
lev_cut = float(np.median([float(r["asset_leverage"] or 0.0) for r in ranked_topics]))
LABELS = ("watch", "on-strategy, no right-to-play",
          "capability looking for a problem", "act")

placement = {
    r["topic_id"]: quadrant(
        float(r["strategic_fit"] or 0.0), float(r["asset_leverage"] or 0.0),
        fit_cut, lev_cut, LABELS,
    )
    for r in ranked_topics
}

print(f"Split at the median of each axis (fit {fit_cut:.3f}, leverage {lev_cut:.3f}).\\n")
for name in ("act", "on-strategy, no right-to-play",
             "capability looking for a problem", "watch"):
    members = [r for r in ranked_topics if placement[r["topic_id"]] == name]
    print(f"{name.upper()} ({len(members)})")
    for r in members:
        print(f"    {r['rank']:>3}  {(r['label'] or r['topic_id'])[:60]}")
    if not members:
        print("    (none)")
    print()
'''

_EVIDENCE = '''
TOPIC_ID = {topic_id!r}
topic = next(r for r in ranked_topics if r["topic_id"] == TOPIC_ID)

print(f"#{{topic['rank']}}  {{topic['label'] or TOPIC_ID}}")
print(f"{{topic['document_count']}} documents · {{topic['first_slice']}}–{{topic['last_slice']}} · "
      f"{{topic['horizon']}} · {{topic['signal_class']}}")
print()
print("Defining terms: " + ", ".join(t for t, _ in (topic["terms"] or [])[:12]))
print()

series = db.fetch_topic_timeseries(conn, TOPIC_ID)
if series:
    peak = max(int(p["doc_count"]) for p in series) or 1
    print("Trajectory (← burst marks slices the Kleinberg automaton flagged):")
    for point in series:
        count = int(point["doc_count"])
        bar = "█" * int(round(24 * count / peak))
        print(f"  {{point['time_slice']:>7}} {{count:5d}}  {{bar}}"
              + ("  ← burst" if point["in_burst"] else ""))
    print()

print("Nearest documents — the primary text every score above derives from.")
print("If these are not a coherent theme, the topic is a clustering artefact")
print("and belongs in the discard pile, whatever it scored:\\n")
for doc in db.fetch_topic_documents(conn, TOPIC_ID, limit={evidence_n}):
    meta = " · ".join(str(x) for x in [doc["source"], doc["year"] or "", doc["venue"] or ""] if x)
    print(f"  • {{(doc['title'] or '(untitled)')[:96]}}")
    print(f"    {{meta[:96]}}")
    if doc["url"]:
        print(f"    {{doc['url']}}")
'''


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------


def _observation_cells(
    builder: NotebookBuilder,
    anchor: str,
    auto: dict[str, list[str]],
    analyst: dict[str, str],
) -> None:
    """Emit the observation block for an anchor, if there is anything to say."""
    derived = auto.get(anchor) or []
    written = analyst.get(anchor)
    if not derived and not written:
        return

    parts: list[str] = []
    if derived:
        parts.append("> **Noted automatically from this run's own numbers.**")
        parts.extend(f">\n> - {note}" for note in derived)
    if written:
        if parts:
            parts.append("")
        parts.append("**Analyst observation**")
        parts.append("")
        parts.append(written)
    builder.markdown("\n".join(parts))


def build(
    conn: duckdb.DuckDBPyConnection,
    config: dict[str, Any],
    run_id: str,
    *,
    execute: bool = True,
    observations: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Assemble (and by default execute) the notebook for *run_id*.

    Returns the notebook document and the list of cell failures, so a caller
    can report a partially-broken export rather than shipping it silently.
    """
    ranked = conn.execute(
        "SELECT topic_id, rank, label FROM topic_scores JOIN topics USING (topic_id, run_id) "
        "WHERE run_id = ? ORDER BY rank",
        [run_id],
    ).fetchall()
    if not ranked:
        raise SystemExit(
            f"No ranked topics for run_id={run_id!r}. Run Stage 5 first "
            f"(python -m src.stage5_synthesis --run-id {run_id})."
        )

    analyst = observations or {}
    auto = auto_observations(conn, config, run_id)

    detailed = int(get(config, "notebook", "topics_detailed", default=8))
    evidence_n = int(
        get(config, "notebook", "evidence_documents_per_topic",
            default=get(config, "synthesis", "evidence_documents_per_topic", default=8))
    )
    verify = bool(get(config, "notebook", "include_verification", default=True))

    db_path = str(get(config, "storage", "duckdb_path", default="data/bigthink.duckdb"))
    generated = datetime.now(timezone.utc)

    nb = NotebookBuilder(execute=execute)

    # --- title ------------------------------------------------------------
    nb.markdown(
        f"""
# IPAVentures horizon scan — run `{run_id}`

Generated {generated:%Y-%m-%d %H:%M} UTC by `python -m src.notebook --run-id {run_id}`.

This notebook is the audit trail for one horizon-scanning run. It reads a
frozen corpus out of DuckDB and walks the pipeline stage by stage: what went
in, what each stage did to it, and what came out. Every code cell is runnable
against the same database, and the outputs shown were produced by running them.

**What this notebook establishes, and what it does not.**

It establishes that the published shortlist follows *deterministically* from
this corpus and these weights. Four numbers — the emergence score, the Three
Horizons band, the opportunity index and the composite rank — are recomputed
from their stored inputs and checked against what the pipeline stored, so the
arithmetic is not something you have to take on trust.

It establishes nothing whatsoever about whether the weights are *right*. No
weight in this pipeline has been validated against a known past opportunity.
The ranking is a hypothesis about ranking, and the useful argument to have over
this document is about the corpus and the weights, not about the arithmetic.

Three caveats that survive every number below:

1. **The opportunity index is not a market size.** It is a relative, unitless,
   within-run ordering and cannot be converted into a dollar figure.
2. **Scores are relative to this run's population.** Emergence and composite
   scores are percentile-ranked within the run; they are not comparable to
   another run's unless the corpus and config snapshot match.
3. **The scan cannot find what the scan frame does not ask for.** A topic's
   absence from this document is not evidence of its absence from the world.

**Reproducing this.** Open the notebook from inside a checkout of the
repository with `{db_path}` present, and run all cells. Stage 1 is
deliberately not re-executed — collectors hit live rate-limited APIs, so
re-running collection would produce a different corpus and quietly invalidate
every comparison below.
"""
    )
    _observation_cells(nb, "overview", auto, analyst)

    # --- setup ------------------------------------------------------------
    nb.markdown("## Setup\n\nOpen the corpus read-only and locate the repository.")
    nb.code(_SETUP.format(run_id=run_id, db_path=db_path))

    # --- provenance -------------------------------------------------------
    nb.markdown(
        """
## Provenance — what produced these numbers

Every stage writes a row to `pipeline_runs` carrying a full snapshot of the
config it ran under. That snapshot, not the file currently on disk, is what
this notebook verifies against: a threshold edited after the run must not be
able to change what "reproduced" means retrospectively.
"""
    )
    nb.code(_PROVENANCE)
    nb.markdown("### Stage execution log\n\nWhat ran, over how much, and how it ended.")
    nb.code(_STAGE_LOG)
    nb.markdown(
        "### Has the repository moved since this run?\n\n"
        "If any value below differs, the code you are reading is not quite the "
        "code that produced these scores."
    )
    nb.code(_CONFIG_DRIFT)
    _observation_cells(nb, "provenance", auto, analyst)

    # --- stage 0 ----------------------------------------------------------
    nb.markdown(
        """
## Stage 0 — Strategy encoding

The published strategy is turned into a reference set: corporate-plan
objectives and initiatives, DISR critical-technology fields, and an inventory
of what IP Australia would bring to an opportunity. Each reference carries a
text body (embedded into a vector) and a lexicon of terms.

This is the yardstick every strategic-fit and asset-leverage score is measured
against, which makes it the most consequential reviewable artefact in the repo
after the scan frame. The files are `data/strategy/*.yaml` and they are meant
to be critiqued without reading any Python.
"""
    )
    nb.code(_STAGE0)
    _observation_cells(nb, "stage0", auto, analyst)

    # --- stage 1 ----------------------------------------------------------
    nb.markdown(
        """
## Stage 1 — Signal collection

Seed queries from `data/strategy/scan_frame.yaml` are issued against each
enabled source; results are normalised, STEEPV-tagged, deduplicated on a stable
`native_id`, and written to DuckDB.

**This stage is not reproducible and is not re-run here.** The collectors call
live APIs under rate limits and daily budgets; a source that was healthy during
the run may be retired or metered now. What follows describes the corpus that
exists, from the collection log the run wrote at the time.

Read this section as the answer to "what could this scan possibly have found?",
because everything downstream is conditioned on it.
"""
    )
    nb.code(_STAGE1_SOURCES)
    nb.code(_STAGE1_SHAPE)
    nb.markdown(
        "### What each seed query actually returned\n\n"
        "`scan_frame.yaml` is the biggest single determinant of the output — the "
        "scan cannot find what the frame does not ask for. A frame that returned "
        "almost nothing is a blind spot in this run's evidence, and no amount of "
        "re-weighting downstream can compensate for it."
    )
    nb.code(_STAGE1_FRAMES)
    _observation_cells(nb, "stage1", auto, analyst)

    # --- stage 2 ----------------------------------------------------------
    nb.markdown(
        """
## Stage 2 — Emergence detection

Documents are embedded, clustered into topics, sliced into a time series, and
scored on the five attributes of emergence from Rotolo, Hicks & Martin (2015):

| Attribute | Indicator |
|---|---|
| novelty | mean embedding distance from the earlier corpus centroid |
| growth | slice-over-slice CAGR blended with Kleinberg burst intensity |
| coherence | intra-topic cosine density |
| impact | citation percentile, computed *within* source |
| uncertainty | normalised entropy over institutions and source types |

Two choices here shape everything after them, and both are deliberate:

- **Impact percentiles are computed within source.** arXiv reports no
  citations. Ranked globally, every preprint would sit at the bottom and the
  fastest-moving evidence in the corpus would be systematically penalised.
- **A logistic curve is fitted directly rather than by linearising the
  logit.** Linearisation reports an early-exponential topic as *saturated*,
  which inverts the Three Horizons band for precisely the technologies a
  horizon scan exists to find.
"""
    )
    nb.code(_STAGE2_TOPICS)
    if verify:
        nb.markdown(
            """
### Re-deriving the emergence score

The score above is not an opaque model output. It is a weighted sum of five
percentile-ranked attributes, and the cell below recomputes it from the stored
attributes and the stored weights.
"""
        )
        nb.code(_STAGE2_VERIFY)
    _observation_cells(nb, "stage2", auto, analyst)

    # --- stage 3 ----------------------------------------------------------
    nb.markdown(
        """
## Stage 3 — Strategic fit and asset leverage

Two scores per topic, each blending an embedding similarity with a lexical
overlap term so that a topic naming an objective explicitly is not penalised by
embedding drift.

- **Strategic fit** — closeness to the Stage 0 objectives and initiatives.
  Answers "is this on strategy?"
- **Asset leverage** — closeness to IP Australia's own data, capability and
  relationship inventory. Answers "could we credibly act on it?", which is the
  question that separates an interesting trend from a viable venture.

Watch the *spread* of each axis below, not just the values. Both are
rank-normalised before they enter the ranking, so a compressed axis still
contributes its full configured share of the ordering while carrying much less
real information than that share implies.
"""
        )
    nb.code(_STAGE3)
    _observation_cells(nb, "stage3", auto, analyst)

    # --- stage 4 ----------------------------------------------------------
    nb.markdown(
        """
## Stage 4 — Opportunity index

**This is a relative, unitless, within-run ordering. It is not a market size,
it is not a dollar figure, and it cannot be converted into one.** This is the
most important caveat in the method and the easiest one to lose in a slide.

Components are percentile-ranked within the run before being combined, which is
the only way measures on this many different scales can be added at all. Two
guards are worth checking in the output below:

- Topics thinner than `opportunity_index.min_documents` are **suppressed, not
  scored**. A composite built on eight documents looks identical to one built
  on eight hundred, and that is exactly how a horizon scan misleads people.
- A component with no data has its **weight redistributed** across the
  components that do. Without that, disabling a source would silently shrink
  every index and leave the ranking looking unchanged while measuring something
  different.

The index is deliberately excluded from the ranking formula in Stage 5. It is
the weakest-founded number in the pipeline, and folding it into the headline
ordering would launder that weakness.
"""
    )
    nb.code(_STAGE4)
    if verify:
        nb.markdown(
            "### Re-deriving the index\n\n"
            "Recomputed from the stored components and the effective weights "
            "actually used, which is also the only way to see the redistribution "
            "having happened rather than take it on trust."
        )
        nb.code(_STAGE4_VERIFY)
    _observation_cells(nb, "stage4", auto, analyst)

    # --- stage 5 ----------------------------------------------------------
    nb.markdown(
        """
## Stage 5 — Ranking and synthesis

The shortlist is a weighted combination of three axes — emergence, strategic
fit and asset leverage — each percentile-ranked within the run before
weighting, so that the configured weights describe what the code actually does.

The opportunity index is deliberately **not** one of the axes. It is the
weakest-founded number in the pipeline, and folding it into the headline
ordering would launder that weakness into the thing everyone reads first.
"""
    )
    if verify:
        nb.markdown(
            "### Re-deriving the published order\n\n"
            "The cell below reproduces the ranking by calling "
            "`stage5_synthesis.composite_scores` — the same function the pipeline "
            "called, not a reimplementation of it, so the check cannot drift away "
            "from the code it is meant to be checking. If it agrees, then given "
            "this corpus and these weights the ordering is not a matter of "
            "opinion. Whether they are the right weights is a separate question, "
            "and an open one."
        )
        nb.code(_STAGE5_VERIFY)
    nb.markdown("### The published shortlist")
    nb.code(_STAGE5_TABLE)
    nb.markdown(
        "### Strategic fit × asset leverage\n\n"
        "The view that separates an interesting trend from a viable venture, "
        "split at the median of each axis. Note that a median split guarantees "
        "a populated *act* quadrant whether or not anything in the run deserves "
        "one — the quadrant is a relative position, not a verdict."
    )
    nb.code(_QUADRANTS)
    _observation_cells(nb, "stage5", auto, analyst)

    # --- evidence ---------------------------------------------------------
    nb.markdown(
        f"""
## Evidence — the documents behind the top {min(detailed, len(ranked))} topics

Reading the primary documents is the cheapest and most reliable quality control
in this method, and the only one that finds clustering artefacts. Some topics
below will not be themes at all; they will be a set of documents that happen to
share vocabulary. Finding those is the point of this section, not a sign that
something has gone wrong.

For each topic: its scores, its defining terms, its trajectory, and the
documents nearest its centre.
"""
    )
    for topic_id, _rank, _label in ranked[:detailed]:
        nb.code(_EVIDENCE.format(topic_id=topic_id, evidence_n=evidence_n))
        note = analyst.get(f"topic:{topic_id}")
        if note:
            nb.markdown(f"**Analyst observation**\n\n{note}")
    _observation_cells(nb, "evidence", auto, analyst)

    # --- closing ----------------------------------------------------------
    nb.markdown(
        """
## What a reviewer should push on

The arithmetic above is checkable and has been checked. The judgement calls are
where this method can actually be wrong, and they are these:

1. **The scan frame.** `data/strategy/scan_frame.yaml` decides what could be
   found at all. Its STEEPV distribution mirrors where free structured data
   exists, not where opportunities are, so the scan will keep finding
   technology trends and keep missing social, values-based and environmental
   ones. A miss caused by the frame cannot be fixed by re-weighting — and
   trying is the standard way to overfit a method like this into uselessness.
2. **The weights.** Every weight was set by reading the literature and
   thinking, not by fitting to a known outcome. The test that would change
   this: take an opportunity IP Australia already pursued, set
   `collection.end_year` to the year before that work began, re-run, and see
   where it lands.
3. **The topics.** Read the evidence sections above and mark the ones that are
   not coherent themes. A clustering artefact scores exactly as confidently as
   a real topic.
4. **The thresholds.** The clustering threshold decides how much of the corpus
   is assigned to any topic at all, and the Three Horizons cut-points decide
   the band a topic is reported in. Both are recorded in the config snapshot
   above; neither is a law of nature.

Where to write the answers: `data/outputs/<run_id>/observations.yaml`. Anything
recorded there is inserted into this notebook as an analyst observation the
next time it is generated, so the reading travels with the numbers.

---

*Method: `docs/method.md`. Live project state, open issues and the calibration
log: `PROJECT_STATE.md`.*
"""
    )
    _observation_cells(nb, "closing", auto, analyst)

    return nb.notebook(f"IPAVentures horizon scan — {run_id}"), nb.failures


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(
    config: dict[str, Any],
    run_id: str,
    *,
    output: Path | None = None,
    execute: bool = True,
) -> Path:
    """Build the notebook for *run_id* and write it into the run's output dir."""
    db_path = resolve_path(config, "storage", "duckdb_path")
    if not db_path.exists():
        raise SystemExit(
            f"No corpus at {db_path}. The notebook explains an existing run; it does "
            f"not create one."
        )

    out_dir = resolve_path(config, "storage", "outputs_dir") / run_id
    observations = load_observations(out_dir / "observations.yaml")

    # Read-only throughout, and closed before the cells run: DuckDB will not
    # hand the same process a read-only handle while a read-write one is open,
    # and the notebook's own cells open the database themselves.
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        document, failures = build(
            conn, config, run_id, execute=execute, observations=observations
        )
    finally:
        conn.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    path = output or (out_dir / f"horizon-scan-{run_id}.ipynb")
    path.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    if failures:
        logger.error(
            "%d notebook cell(s) failed; their tracebacks are embedded in %s: %s",
            len(failures), path.name, "; ".join(failures[:3]),
        )
    logger.info("Wrote %s (%d cells)", path, len(document["cells"]))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export a run as a peer-reviewable Jupyter notebook."
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", default=None, help="Write to this path instead.")
    parser.add_argument(
        "--no-execute", action="store_true",
        help="Emit cells without running them (no embedded outputs).",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)-7s %(message)s")
    path = run(
        load_config(args.config),
        args.run_id,
        output=Path(args.output) if args.output else None,
        execute=not args.no_execute,
    )
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
