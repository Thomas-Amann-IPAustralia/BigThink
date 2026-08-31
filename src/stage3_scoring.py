"""
src/stage3_scoring.py — Stage 3: Strategic fit and asset leverage.

Scores every Stage 2 topic on the two axes that decide whether an emerging
trend is an opportunity *for IP Australia specifically*:

  strategic fit    How close is this to what the agency has said it will do?
                   Cosine similarity of the topic centroid to the Stage 0
                   objective and initiative vectors, blended with lexical
                   overlap, plus a bonus for DISR critical-technology alignment.

  asset leverage   What would the agency bring that others could not? Same
                   blend, against the asset inventory.

WHY BOTH EMBEDDING AND LEXICON

Embeddings catch a topic that means the same thing in different words. Lexical
overlap catches a topic that names the thing exactly — "geographical
indications" should score against initiative SI-1 on the strength of the phrase
alone, and an embedding of a whole paragraph can dilute that to nothing. The
blend weights are in `scoring.strategic_fit` / `scoring.asset_leverage`.

WHAT THIS STAGE DELIBERATELY DOES NOT DO

It does not touch Stage 2 output. Emergence and fit are separate measurements
of separate things, and a topic must be able to score high on one and low on
the other — that separation is the entire point of the 2x2. Re-running Stage 3
overwrites only `topic_scores`.

Run:
    python -m src.stage3_scoring --run-id 2026-08-29
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Sequence

import numpy as np

from src import db
from src.config import (
    critical_tech_match_threshold,
    critical_tech_match_weights,
    get,
    load_config,
    resolve_path,
    snapshot_config,
)
from src.embeddings import build_embedder, encode_with_cache, normalise_tokens
from src.errors import insufficient_data_error

logger = logging.getLogger(__name__)

STAGE = "stage3_scoring"


# ---------------------------------------------------------------------------
# Lexical matching
# ---------------------------------------------------------------------------


def lexicon_overlap(topic_terms: Sequence[tuple[str, float]], lexicon: Sequence[str]) -> float:
    """Weighted overlap between a topic's top terms and a reference lexicon.

    Matching is on normalised tokens rather than raw strings, so "trade marks"
    matches "trade mark" and "Automated Decision Making" matches "automated
    decision making". A multi-word lexicon entry counts as matched when all its
    tokens appear among the topic's tokens — partial phrase matches are the
    main source of false positives here.

    Scores are normalised by the topic's own term weight, so a topic is not
    rewarded merely for having many terms.
    """
    if not topic_terms or not lexicon:
        return 0.0

    topic_tokens: set[str] = set()
    weight_by_token: dict[str, float] = {}
    total_weight = 0.0
    for term, weight in topic_terms:
        tokens = normalise_tokens(term)
        topic_tokens.update(tokens)
        total_weight += float(weight)
        for token in tokens:
            weight_by_token[token] = weight_by_token.get(token, 0.0) + float(weight)

    if total_weight <= 0:
        return 0.0

    matched = 0.0
    for entry in lexicon:
        tokens = normalise_tokens(entry)
        if not tokens:
            continue
        if all(token in topic_tokens for token in tokens):
            # Credit the phrase by the weight of its rarest constituent, so a
            # two-word match is not double-counted.
            matched += min(weight_by_token.get(token, 0.0) for token in tokens)

    return float(np.clip(matched / total_weight, 0.0, 1.0))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_against_refs(
    topic_vector: np.ndarray,
    topic_terms: Sequence[tuple[str, float]],
    refs: Sequence[dict[str, Any]],
    ref_vectors: np.ndarray,
    *,
    embedding_weight: float,
    lexicon_weight: float,
) -> tuple[float, str, float]:
    """Score a topic against a set of references.

    Returns (best blended score, best reference label, best raw cosine).

    The topic takes the score of its single best-matching reference, not the
    mean across all of them. A trend that speaks directly to one objective is a
    strong strategic fit; averaging that against eight unrelated objectives
    would bury it, and would make every topic converge on the same middling
    score.
    """
    if len(refs) == 0 or ref_vectors.size == 0:
        return 0.0, "", 0.0

    similarities = ref_vectors @ topic_vector          # (n_refs,)
    best_score, best_label, best_similarity = 0.0, "", 0.0

    for i, ref in enumerate(refs):
        cosine = float(max(similarities[i], 0.0))
        lexical = lexicon_overlap(topic_terms, ref.get("lexicon", []))
        blended = embedding_weight * cosine + lexicon_weight * lexical
        # The reference's own weight expresses relative priority — Strategic
        # Objective 4.1 matters more to this lab than 3.2 does.
        blended *= float(ref.get("weight", 1.0))
        if blended > best_score:
            best_score, best_label, best_similarity = blended, _ref_label(ref), cosine

    return float(np.clip(best_score, 0.0, 1.0)), best_label, best_similarity


def _ref_label(ref: dict[str, Any]) -> str:
    code = ref.get("code")
    return f"{code} {ref['label']}" if code else str(ref["label"])


def critical_technology_scores(
    topic_vector: np.ndarray,
    topic_terms: Sequence[tuple[str, float]],
    refs: Sequence[dict[str, Any]],
    ref_vectors: np.ndarray,
    *,
    embedding_weight: float = 0.7,
    lexicon_weight: float = 0.3,
) -> list[float]:
    """Blended score of a topic against each DISR critical-technology field.

    Split out from the match itself so `python -m src.calibrate critical-tech`
    can sweep a cut-off over the same numbers the pipeline compares against,
    rather than reimplementing the blend and drifting away from it.
    """
    if not refs or ref_vectors.size == 0:
        return []
    similarities = ref_vectors @ topic_vector
    return [
        embedding_weight * float(max(similarities[i], 0.0))
        + lexicon_weight * lexicon_overlap(topic_terms, ref.get("lexicon", []))
        for i, ref in enumerate(refs)
    ]


def match_critical_technology(
    topic_vector: np.ndarray,
    topic_terms: Sequence[tuple[str, float]],
    refs: Sequence[dict[str, Any]],
    ref_vectors: np.ndarray,
    *,
    threshold: float | None,
    embedding_weight: float = 0.7,
    lexicon_weight: float = 0.3,
) -> str | None:
    """Return the best-matching DISR critical technology field, or None.

    A binary flag rather than a continuous score: the DISR list is a policy
    designation, and a topic either falls in a national-interest field or it
    does not. The threshold keeps weak matches from collecting the bonus.

    `threshold=None` means the active embedding backend has no swept cut-off,
    and the answer is then None for every topic — no match, no bonus. That is
    deliberate and it is the whole point of making the parameter required.
    A cut-off carried over from another vector space does not degrade
    gracefully: 0.25 was a bare Python default here until 2026-08-31, and on
    the only run ever produced under the shipped `bge` + `bertopic` default it
    matched 114 of 114 topics. Every evidence card printed a national-interest
    designation that meant nothing, and every topic collected the bonus.
    Reporting no match is a smaller error than reporting a false one, and
    unlike a false one it is visible. See PROJECT_STATE.md issue 21.
    """
    if threshold is None:
        return None
    scores = critical_technology_scores(
        topic_vector, topic_terms, refs, ref_vectors,
        embedding_weight=embedding_weight, lexicon_weight=lexicon_weight,
    )
    if not scores:
        return None
    best = int(np.argmax(scores))
    return _ref_label(refs[best]) if scores[best] >= threshold else None


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------


def run(config: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    conn = db.init_db(resolve_path(config, "storage", "duckdb_path"))
    entry_id = db.log_stage_start(conn, run_id, STAGE, snapshot_config(config))
    try:
        scores = _run_inner(conn, config, run_id)
        db.log_stage_finish(
            conn, entry_id, "success",
            records_in=len(scores), records_out=len(scores),
            message=(
                f"scored {len(scores)} topics; "
                f"mean fit {np.mean([s['strategic_fit'] for s in scores]):.3f}, "
                f"mean leverage {np.mean([s['asset_leverage'] for s in scores]):.3f}, "
                f"{sum(1 for s in scores if s['critical_tech'])} critical-tech matches"
                + (
                    " (matching DISABLED — no swept threshold for this backend)"
                    if critical_tech_match_threshold(config) is None else ""
                )
            ),
        )
        return scores
    except Exception as exc:
        db.log_stage_finish(conn, entry_id, "failed", message=str(exc))
        raise
    finally:
        conn.close()


def _run_inner(conn: Any, config: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    topics = db.fetch_topics(conn, run_id)
    if not topics:
        raise insufficient_data_error(
            STAGE, f"no topics found for run_id={run_id!r}. Run Stage 2 first."
        )

    refs = db.fetch_strategy_refs(conn)
    if not refs:
        raise insufficient_data_error(
            STAGE, "no strategy references found. Run Stage 0 first."
        )

    strategy_refs = [r for r in refs if r["ref_type"] in ("objective", "initiative")]
    critical_refs = [r for r in refs if r["ref_type"] == "critical_tech"]
    asset_refs = [r for r in refs if r["ref_type"] == "asset"]

    # Fitted on documents + reference texts so that topic vectors and reference
    # vectors share one IDF, which is what makes the cosine between them mean
    # anything. Note this is NOT the same fit Stage 2 used (documents alone), and
    # it does not need to be: nothing here is compared against a Stage 2 vector.
    # Everything Stage 3 scores, it encodes itself, within this one space.
    documents = db.fetch_documents(conn)
    from src.collectors.base import document_text

    corpus = [document_text(d) for d in documents] + [r["text"] for r in refs]
    embedder = build_embedder(config)
    embedder.fit(corpus)
    cache = bool(get(config, "embeddings", "cache_vectors", default=True))

    def vectors_for(items: Sequence[dict[str, Any]]) -> np.ndarray:
        if not items:
            return np.zeros((0, embedder.dimensions))
        return encode_with_cache(embedder, [r["text"] for r in items], conn, enabled=cache)

    strategy_vectors = vectors_for(strategy_refs)
    critical_vectors = vectors_for(critical_refs)
    asset_vectors = vectors_for(asset_refs)

    # A topic is represented by its label terms rather than its member
    # documents: the terms are what the topic *is about*, while the documents
    # carry a lot of shared academic boilerplate that pulls every topic vector
    # toward the same place.
    topic_texts = [
        " ".join([t["label"] or ""] + [term for term, _ in t["terms"]]) for t in topics
    ]
    topic_vectors = encode_with_cache(embedder, topic_texts, conn, enabled=cache)

    fit_cfg = get(config, "scoring", "strategic_fit", default={}) or {}
    lev_cfg = get(config, "scoring", "asset_leverage", default={}) or {}
    bonus = float(fit_cfg.get("critical_tech_bonus", 0.10))

    ct_threshold = critical_tech_match_threshold(config)
    ct_embedding_weight, ct_lexicon_weight = critical_tech_match_weights(config)
    if ct_threshold is None:
        # Said once, loudly, rather than left to be inferred from a column of
        # dashes. The alternative — reusing the `hashing` cut-off under `bge` —
        # is what produced a 100% match rate on verify-bertopic (issue 21).
        logger.warning(
            "No critical-technology match threshold is configured for the active "
            "embedding backend %r, so NO topic will be matched to a DISR field and "
            "none will receive the %.2f critical_tech_bonus. This is deliberate: a "
            "cut-off swept under one backend does not transfer to another. Sweep it "
            "with `python -m src.calibrate critical-tech` and set "
            "scoring.strategic_fit.critical_tech_match.thresholds.%s.",
            str(get(config, "embeddings", "backend", default="hashing")),
            bonus,
            str(get(config, "embeddings", "backend", default="hashing")),
        )

    scores: list[dict[str, Any]] = []
    for i, topic in enumerate(topics):
        terms = [(str(t), float(w)) for t, w in topic["terms"]]
        vector = topic_vectors[i]

        fit, best_objective, best_sim = score_against_refs(
            vector, terms, strategy_refs, strategy_vectors,
            embedding_weight=float(fit_cfg.get("embedding_weight", 0.7)),
            lexicon_weight=float(fit_cfg.get("lexicon_weight", 0.3)),
        )
        critical = match_critical_technology(
            vector, terms, critical_refs, critical_vectors,
            threshold=ct_threshold,
            embedding_weight=ct_embedding_weight,
            lexicon_weight=ct_lexicon_weight,
        )
        if critical:
            fit = float(np.clip(fit + bonus, 0.0, 1.0))

        leverage, best_asset, _ = score_against_refs(
            vector, terms, asset_refs, asset_vectors,
            embedding_weight=float(lev_cfg.get("embedding_weight", 0.6)),
            lexicon_weight=float(lev_cfg.get("lexicon_weight", 0.4)),
        )

        scores.append(
            {
                "topic_id": topic["topic_id"],
                "strategic_fit": fit,
                "best_objective": best_objective,
                "best_objective_sim": best_sim,
                "critical_tech": critical,
                "asset_leverage": leverage,
                "best_asset": best_asset,
            }
        )

    logger.info(
        "Stage 3 scored %d topics against %d strategy refs, %d critical-tech fields, "
        "%d assets", len(scores), len(strategy_refs), len(critical_refs), len(asset_refs),
    )
    return scores


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 3 — strategic fit and asset leverage.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    config = load_config(args.config)
    scores = run(config, args.run_id)

    # Stage 3 alone does not write topic_scores — Stage 4 completes the row.
    # Printing here lets you inspect fit before the index is built on top.
    print(f"\n{'topic':7s} {'fit':>5s} {'lev':>5s}  {'objective':38s} {'asset':28s} critical-tech")
    for s in sorted(scores, key=lambda x: -x["strategic_fit"])[:20]:
        print(
            f"{s['topic_id']:7s} {s['strategic_fit']:5.3f} {s['asset_leverage']:5.3f}  "
            f"{s['best_objective'][:36]:38s} {s['best_asset'][:26]:28s} "
            f"{s['critical_tech'] or '-'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
