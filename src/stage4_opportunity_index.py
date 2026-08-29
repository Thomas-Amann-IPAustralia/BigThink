"""
src/stage4_opportunity_index.py — Stage 4: Opportunity-size index.

Builds a composite, RELATIVE index of how large an opportunity a topic might
represent.

READ THIS BEFORE USING ANY NUMBER THIS STAGE PRODUCES.

This is not a market size. It is not a dollar figure. It cannot be converted
into one. McKinsey-style value pools are bottom-up gross-margin models built
from segment-level expert assumptions, and there is no free feed for them; the
proposal this pipeline implements says so explicitly, and the caveat is the
most important one in the document.

What this stage produces is an ordering: given two topics in the same run,
which one has more of the signals that usually accompany a large opportunity?
Components (weights in `opportunity_index.components`):

  research_growth   publication growth percentile
  attention         GDELT news volume percentile
  attention_tone    GDELT tone, rescaled — positive coverage as a proxy for
                    receptiveness rather than controversy
  policy_salience   how often the topic's terms appear in the strategy corpus
  patent_activity   patent filing growth percentile (zero without PatentsView)

Everything is percentile-ranked within the run before combining, so components
on wildly different scales can be added at all. That also means the index is
only ever comparable *within* a run — an index of 0.8 last month and 0.8 this
month say nothing about each other.

TWO GUARDS THAT MATTER

  * Thin topics are suppressed, not scored. A composite built on eight
    documents looks exactly like one built on eight hundred, and that is
    precisely how a horizon scan misleads people. Below
    `opportunity_index.min_documents` the index is NULL and flagged.

  * Missing components have their weight redistributed across the components
    that do have data. Without this, disabling PatentsView would silently
    shrink every topic's index by 15% and the ranking would look unchanged
    while meaning something different.

Run:
    python -m src.stage4_opportunity_index --run-id 2026-08-29
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Sequence

import numpy as np

from src import db
from src.config import get, load_config, resolve_path, snapshot_config
from src.embeddings import normalise_tokens
from src.errors import insufficient_data_error
from src.stage0_strategy import load_strategy_corpus

logger = logging.getLogger(__name__)

STAGE = "stage4_opportunity_index"

# Sources that count as research vs attention vs patents when splitting a
# topic's documents into component signals.
RESEARCH_SOURCES = frozenset({"openalex", "crossref", "arxiv"})
ATTENTION_SOURCES = frozenset({"gdelt"})
PATENT_SOURCES = frozenset({"patentsview"})


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


def percentile_rank(values: Sequence[float]) -> list[float]:
    """Rank values into [0, 1] by their position in the population.

    Ties share the mean of the positions they span, so a population where most
    topics score zero does not hand all of them a spurious 0.5.
    """
    array = np.asarray(values, dtype=np.float64)
    n = len(array)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    order = np.argsort(array, kind="stable")
    ranks = np.empty(n, dtype=np.float64)
    ranks[order] = np.arange(n, dtype=np.float64)

    # Average ranks within tie groups.
    sorted_values = array[order]
    start = 0
    for i in range(1, n + 1):
        if i == n or sorted_values[i] != sorted_values[start]:
            ranks[order[start:i]] = (start + i - 1) / 2.0
            start = i
    return [float(r / (n - 1)) for r in ranks]


def policy_salience(
    terms: Sequence[tuple[str, float]], corpus_tokens: dict[str, int], corpus_size: int
) -> float:
    """How present a topic's vocabulary is in IP Australia's strategy documents.

    A weighted term-frequency lookup, normalised by corpus size. A topic whose
    terms are all over the Corporate Plan is one the agency has already
    committed to; one that appears nowhere is either a genuine blind spot or
    irrelevant, and Stage 5's human session is where that gets decided.
    """
    if not terms or corpus_size <= 0:
        return 0.0
    total_weight = sum(float(w) for _, w in terms) or 1.0
    score = 0.0
    for term, weight in terms:
        tokens = normalise_tokens(term)
        if not tokens:
            continue
        # A phrase is as present as its rarest word — the limiting factor.
        occurrences = min(corpus_tokens.get(t, 0) for t in tokens)
        if occurrences:
            score += float(weight) * min(occurrences / 50.0, 1.0)
    return float(np.clip(score / total_weight, 0.0, 1.0))


def build_corpus_tokens(corpus: str) -> tuple[dict[str, int], int]:
    """Token frequency map for the strategy corpus."""
    tokens = normalise_tokens(corpus)
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return counts, len(tokens)


def redistribute_weights(
    weights: dict[str, float], available: set[str]
) -> dict[str, float]:
    """Rescale weights across the components that actually have data.

    Returns weights summing to 1.0 over `available`. If nothing is available,
    returns an empty dict and the caller must suppress the index rather than
    emit a zero — a zero index and an unmeasurable one are different claims.
    """
    usable = {k: float(v) for k, v in weights.items() if k in available and float(v) > 0}
    total = sum(usable.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in usable.items()}


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------


def run(config: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    conn = db.init_db(resolve_path(config, "storage", "duckdb_path"))
    entry_id = db.log_stage_start(conn, run_id, STAGE, snapshot_config(config))
    try:
        result = _run_inner(conn, config, run_id)
        db.log_stage_finish(
            conn, entry_id, "success",
            records_in=len(result), records_out=len(result),
            message=(
                f"indexed {sum(1 for r in result if not r['index_suppressed'])} of "
                f"{len(result)} topics "
                f"({sum(1 for r in result if r['index_suppressed'])} suppressed as too thin)"
            ),
        )
        return result
    except Exception as exc:
        db.log_stage_finish(conn, entry_id, "failed", message=str(exc))
        raise
    finally:
        conn.close()


def _run_inner(conn: Any, config: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    topics = db.fetch_topics(conn, run_id)
    if not topics:
        raise insufficient_data_error(
            STAGE, f"no topics for run_id={run_id!r}. Run Stage 2 first."
        )

    weights = dict(get(config, "opportunity_index", "components", default={}) or {})
    min_documents = int(get(config, "opportunity_index", "min_documents", default=15))

    corpus_tokens, corpus_size = build_corpus_tokens(load_strategy_corpus(config))

    # --- raw per-topic component values -----------------------------------
    raw: dict[str, list[float]] = {k: [] for k in weights}
    have_data: dict[str, bool] = {k: False for k in weights}

    for topic in topics:
        docs = db.fetch_topic_documents(conn, topic["topic_id"], limit=100000)
        by_source: dict[str, list[dict[str, Any]]] = {}
        for doc in docs:
            by_source.setdefault(doc["source"], []).append(doc)

        research = sum(len(by_source.get(s, [])) for s in RESEARCH_SOURCES)
        attention = sum(len(by_source.get(s, [])) for s in ATTENTION_SOURCES)
        patents = sum(len(by_source.get(s, [])) for s in PATENT_SOURCES)

        tones = [
            float(d["tone"]) for d in docs
            if d.get("tone") is not None and d["source"] in ATTENTION_SOURCES
        ]

        # research_growth reuses the CAGR Stage 2 already fitted, scaled by how
        # much of the topic is research evidence at all.
        cagr = float(topic.get("cagr") or 0.0)
        research_share = research / max(len(docs), 1)

        values = {
            "research_growth": max(cagr, 0.0) * research_share,
            "attention": float(attention),
            # GDELT tone runs roughly -10..+10; rescale to [0, 1] so the
            # percentile step is not dominated by sign.
            "attention_tone": float(np.clip((np.mean(tones) + 10.0) / 20.0, 0.0, 1.0))
            if tones else 0.0,
            "policy_salience": policy_salience(topic["terms"], corpus_tokens, corpus_size),
            "patent_activity": float(patents),
        }
        for key in raw:
            raw[key].append(values.get(key, 0.0))

        if research:
            have_data["research_growth"] = have_data.get("research_growth", False) or True
        if attention:
            have_data["attention"] = True
        if tones:
            have_data["attention_tone"] = True
        if patents:
            have_data["patent_activity"] = True

    # policy_salience always has data when a strategy corpus loaded.
    if corpus_size > 0 and "policy_salience" in have_data:
        have_data["policy_salience"] = True

    available = {k for k, present in have_data.items() if present}
    missing = set(weights) - available
    effective = redistribute_weights(weights, available)
    if not effective:
        raise insufficient_data_error(
            STAGE,
            "no opportunity-index component has any data. Check that Stage 1 collected "
            "from at least one source and that the strategy documents loaded.",
        )
    if missing:
        logger.warning(
            "No data for index component(s) %s — their weight has been redistributed "
            "across %s. The index remains internally consistent but measures fewer "
            "things than the config describes; say so when presenting it.",
            sorted(missing), sorted(effective),
        )

    # --- percentile-rank, then combine ------------------------------------
    ranked = {key: percentile_rank(values) for key, values in raw.items()}

    results: list[dict[str, Any]] = []
    for i, topic in enumerate(topics):
        components = {key: round(ranked[key][i], 4) for key in effective}
        suppressed = int(topic["document_count"]) < min_documents
        index = (
            None if suppressed
            else float(sum(components[k] * w for k, w in effective.items()))
        )
        results.append(
            {
                "topic_id": topic["topic_id"],
                "opportunity_index": index,
                "index_components": {
                    **components,
                    "_effective_weights": {k: round(v, 4) for k, v in effective.items()},
                    "_omitted_components": sorted(missing),
                },
                "index_suppressed": suppressed,
            }
        )

    suppressed_count = sum(1 for r in results if r["index_suppressed"])
    if suppressed_count:
        logger.info(
            "%d of %d topics have fewer than %d documents; their opportunity index is "
            "suppressed rather than estimated.",
            suppressed_count, len(results), min_documents,
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 4 — relative opportunity index (NOT a market size)."
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    results = run(load_config(args.config), args.run_id)
    scored = [r for r in results if not r["index_suppressed"]]
    print(f"\n{len(scored)} topics indexed, {len(results) - len(scored)} suppressed as too thin.")
    for r in sorted(scored, key=lambda x: -(x["opportunity_index"] or 0))[:15]:
        comps = {k: v for k, v in r["index_components"].items() if not k.startswith("_")}
        print(f"  {r['topic_id']:7s} index={r['opportunity_index']:.3f}  {comps}")
    print("\nThis index is RELATIVE and unitless. It is not a market size.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
