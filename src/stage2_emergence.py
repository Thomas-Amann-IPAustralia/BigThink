"""
src/stage2_emergence.py — Stage 2: Trend and emergence detection.

The analytical core. Turns a pile of collected documents into scored topics:

  1. Embed the corpus (src/embeddings.py)
  2. Form topics (src/topics.py)
  3. Build a per-topic time series
  4. Kleinberg burst detection on each series (src/burst.py)
  5. Fit a logistic growth curve -> maturity -> Three Horizons band
  6. Compute the five Rotolo et al. (2015) emergence attributes
  7. Classify each topic as a weak / strong / latent / noise signal

THE ROTOLO EMERGENCE SCORE

Rotolo, Hicks & Martin (2015), "What is an emerging technology?", Research
Policy 44(10):1827-1843, define emergence through five attributes, verbatim:
"(i) radical novelty, (ii) relatively fast growth, (iii) coherence,
(iv) prominent impact, and (v) uncertainty and ambiguity."

Each is operationalised here as one computable indicator in [0, 1]:

  novelty      mean cosine distance from the topic centroid to the centroid of
               the earliest time slices — how far this sits from where the
               field already was
  growth       slice-over-slice CAGR, blended with Kleinberg burst intensity
  coherence    mean cosine of members to their own centroid (cluster density)
  impact       citation percentile within the corpus, computed per source so
               preprints are ranked against preprints
  uncertainty  normalised entropy over actors and source types — a topic whose
               participants are dispersed across many institutions and
               document types is more ambiguous than one owned by three labs

They are combined with the weights in `emergence.rotolo_weights`.

THE WEIGHTS ARE A JUDGEMENT, NOT A FINDING. They were set by reading Rotolo,
not by fitting to a known outcome. The caveat in the proposal applies directly:
validate against a past opportunity before trusting the ranking. See
PROJECT_STATE.md, "Open calibration decisions".

Run:
    python -m src.stage2_emergence --run-id 2026-08-29
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from collections import Counter, defaultdict
from typing import Any, Sequence

import numpy as np

from src import db, topics as topics_mod
from src.burst import burst_score, detect_bursts
from src.collectors.base import document_text
from src.config import (
    get,
    load_config,
    resolve_path,
    snapshot_config,
    topic_similarity_threshold,
)
from src.embeddings import build_embedder, centroid, encode_with_cache
from src.errors import insufficient_data_error

logger = logging.getLogger(__name__)

STAGE = "stage2_emergence"


# ---------------------------------------------------------------------------
# Time series
# ---------------------------------------------------------------------------


def corpus_slices(documents: Sequence[dict[str, Any]]) -> list[str]:
    """Every time slice present in the corpus, in order."""
    return sorted({d["time_slice"] for d in documents if d.get("time_slice")})


def slice_totals(documents: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Total documents per slice — the denominator burst detection needs."""
    totals: Counter[str] = Counter()
    for doc in documents:
        if doc.get("time_slice"):
            totals[doc["time_slice"]] += 1
    return dict(totals)


def topic_series(
    member_docs: Sequence[dict[str, Any]], all_slices: Sequence[str]
) -> list[int]:
    """Documents per slice for one topic, aligned to the corpus slice list.

    Zero-filled across the full corpus span, not just the topic's own span:
    a topic absent until 2024 must show zeros for 2018-2023, or its growth is
    measured against a start point that does not exist.
    """
    counts: Counter[str] = Counter(
        d["time_slice"] for d in member_docs if d.get("time_slice")
    )
    return [counts.get(s, 0) for s in all_slices]


# ---------------------------------------------------------------------------
# Growth and maturity
# ---------------------------------------------------------------------------


def compute_cagr(series: Sequence[float], min_slices: int = 4) -> float:
    """Compound annual growth rate over the series.

    Endpoints are smoothed with their neighbours before the ratio is taken —
    a raw first-to-last CAGR on noisy annual counts is dominated by whichever
    single year happened to be quiet.

    Returns 0.0 when there is too little series to say anything.
    """
    values = [float(v) for v in series]
    if len(values) < min_slices or sum(values) <= 0:
        return 0.0

    head = values[:2]
    tail = values[-2:]
    start = sum(head) / len(head)
    end = sum(tail) / len(tail)
    periods = len(values) - 1
    if periods <= 0:
        return 0.0

    # A topic starting from nothing has an undefined CAGR. Substituting 0.5
    # (half a document) keeps it finite and ranks true zero-to-many topics
    # above slower risers, which is the behaviour we want.
    start = max(start, 0.5)
    if end <= 0:
        return -1.0
    return float((end / start) ** (1.0 / periods) - 1.0)


def fit_logistic_maturity(series: Sequence[float]) -> tuple[float, float]:
    """Fit a logistic curve to cumulative counts; return (maturity, fit_quality).

    Maturity is the fitted position along the S-curve at the last slice, in
    [0, 1]: near 0 means the technology has barely started, near 1 means it has
    saturated. That is what places a topic on a Three Horizons band — Three
    Horizons is about where something sits on its own growth curve, not how old
    it is.

    METHOD. Direct least-squares fit of

        y(t) = K / (1 + exp(-r * (t - t0)))

    by coarse grid search over (K, r, t0), scored by R-squared on the
    cumulative counts. No scipy, and the search space is inspectable.

    Two details that a logit-linearisation gets wrong, and which cost real
    accuracy here:

      * Leading zeros. A topic absent for its first three slices contributes
        cumulative zeros, which linearisation must clip to a floor; the clipped
        points then act as extreme outliers and dominate the fit. They are
        trimmed instead, and the curve is fitted from the slice the topic
        actually starts in.

      * Early exponentials. For a series still in its exponential phase, the
        logit of y/K is near-linear for *any* sufficiently large K, so
        maximising linearity picks the smallest K and reports a young topic as
        saturated — exactly backwards, and exactly the case Three Horizons
        exists to identify. Fitting the curve itself has no such degeneracy.

    Returns (0.5, 0.0) when the series is too short or too flat to fit — a
    neutral maturity with zero fit quality, so a caller can tell a real fit
    from a fallback.
    """
    cumulative = np.cumsum(np.asarray(series, dtype=np.float64))
    if len(cumulative) < 4 or cumulative[-1] <= 0:
        return 0.5, 0.0

    # Fit from the slice the topic first appears in.
    first = int(np.argmax(cumulative > 0))
    y = cumulative[first:]
    if len(y) < 4:
        return 0.5, 0.0

    n = len(y)
    t = np.arange(n, dtype=np.float64)
    observed = float(y[-1])
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    if ss_tot <= 0:
        return 0.5, 0.0

    # K from "already saturated" to "we have seen 5% of the eventual total".
    multipliers = np.concatenate([np.arange(1.0, 3.0, 0.1), np.arange(3.0, 20.5, 0.5)])
    rates = np.arange(0.2, 3.01, 0.1)                # growth rate
    inflections = np.arange(-n, 2.0 * n, 0.5)        # inflection position

    # Evaluated as one broadcast per K rather than a triple Python loop:
    # (rates, inflections, slices). Same grid, ~50x faster, which matters when
    # this runs once per topic.
    logistic = 1.0 / (
        1.0 + np.exp(-rates[:, None, None] * (t[None, None, :] - inflections[None, :, None]))
    )                                                 # (R, T0, n)

    best_r2, best_maturity = -np.inf, 0.5
    for multiplier in multipliers:
        K = observed * float(multiplier)
        residuals = y[None, None, :] - K * logistic
        ss_res = np.sum(residuals * residuals, axis=2)   # (R, T0)
        r2 = 1.0 - float(ss_res.min()) / ss_tot
        if r2 > best_r2:
            best_r2, best_maturity = r2, observed / K

    r2, maturity = best_r2, best_maturity
    if r2 < 0.0:
        return 0.5, 0.0
    return float(np.clip(maturity, 0.0, 1.0)), float(r2)


def assign_horizon(maturity: float, config: dict[str, Any]) -> str:
    """Place a topic on a Three Horizons band from its curve position."""
    h1 = float(get(config, "emergence", "three_horizons", "h1_max_maturity", default=0.75))
    h2 = float(get(config, "emergence", "three_horizons", "h2_max_maturity", default=0.35))
    if maturity >= h1:
        return "H1"   # 0-3 yrs: established, sustains the current system
    if maturity >= h2:
        return "H2"   # transitional / emerging innovation
    return "H3"       # 10-30 yrs: potential paradigm shift


# ---------------------------------------------------------------------------
# Rotolo attributes
# ---------------------------------------------------------------------------


def compute_novelty(
    topic_centroid: np.ndarray, early_centroid: np.ndarray
) -> float:
    """Radical novelty: cosine distance from where the field already was.

    An early-corpus centroid, not a global one: measuring against the whole
    corpus would make a topic look less novel simply because it exists in it.
    """
    if topic_centroid is None or not np.any(topic_centroid) or not np.any(early_centroid):
        return 0.5
    similarity = float(np.dot(topic_centroid, early_centroid))
    return float(np.clip((1.0 - similarity) / 2.0 + 0.5 * (1.0 - similarity), 0.0, 1.0))


def compute_coherence(vectors: np.ndarray, topic_centroid: np.ndarray) -> float:
    """Cluster density: mean cosine of members to their own centroid."""
    if len(vectors) == 0 or topic_centroid is None or not np.any(topic_centroid):
        return 0.0
    return float(np.clip(np.mean(vectors @ topic_centroid), 0.0, 1.0))


def compute_impact(
    member_docs: Sequence[dict[str, Any]], percentiles_by_source: dict[str, dict[int, float]]
) -> float:
    """Prominent impact: mean citation percentile of the topic's documents.

    Percentiles are computed within each source, so an arXiv preprint (which
    reports no citations at all) is ranked against other preprints rather than
    dragging the topic down against journal articles. Cross-source citation
    counts are not comparable and treating them as such would systematically
    penalise the fastest-moving evidence in the corpus.
    """
    if not member_docs:
        return 0.0
    scores = [
        percentiles_by_source.get(d["source"], {}).get(int(d.get("citation_count") or 0), 0.0)
        for d in member_docs
    ]
    return float(np.clip(np.mean(scores), 0.0, 1.0)) if scores else 0.0


def compute_uncertainty(member_docs: Sequence[dict[str, Any]]) -> float:
    """Uncertainty and ambiguity: dispersion of actors and source types.

    Two normalised entropies, averaged: over institutions (who is working on
    this) and over sources (what kinds of evidence exist for it). A topic
    concentrated in three labs and one source type is a well-defined research
    programme; one spread across many actors and evidence types is ambiguous in
    exactly the sense Rotolo means.
    """
    if not member_docs:
        return 0.0

    institutions: Counter[str] = Counter()
    for doc in member_docs:
        for name in _as_list(doc.get("institutions")):
            institutions[name] += 1
    sources: Counter[str] = Counter(d["source"] for d in member_docs)

    parts = [_normalised_entropy(sources)]
    if institutions:
        parts.append(_normalised_entropy(institutions))
    return float(np.clip(sum(parts) / len(parts), 0.0, 1.0))


def _normalised_entropy(counter: Counter[str]) -> float:
    """Shannon entropy scaled to [0, 1] by the maximum for that many categories."""
    total = sum(counter.values())
    if total <= 0 or len(counter) < 2:
        return 0.0
    probabilities = np.array([c / total for c in counter.values()])
    entropy = -float(np.sum(probabilities * np.log(probabilities)))
    return entropy / math.log(len(counter))


def _as_list(value: Any) -> list[str]:
    """DuckDB round-trips JSON columns as strings; accept either form."""
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str) and value.strip().startswith("["):
        import json

        try:
            return [str(v) for v in json.loads(value) if v]
        except ValueError:
            return []
    return []


def citation_percentiles(documents: Sequence[dict[str, Any]]) -> dict[str, dict[int, float]]:
    """Per-source map from citation count to its percentile within that source."""
    by_source: dict[str, list[int]] = defaultdict(list)
    for doc in documents:
        by_source[doc["source"]].append(int(doc.get("citation_count") or 0))

    result: dict[str, dict[int, float]] = {}
    for source, counts in by_source.items():
        ordered = np.sort(np.asarray(counts, dtype=np.float64))
        unique = sorted(set(counts))
        # Fraction of documents at or below this count.
        result[source] = {
            value: float(np.searchsorted(ordered, value, side="right") / len(ordered))
            for value in unique
        }
    return result


# ---------------------------------------------------------------------------
# Signal classification
# ---------------------------------------------------------------------------


def classify_signal(
    avg_proportion: float,
    growth: float,
    proportion_cut: float,
    growth_cut: float,
) -> str:
    """Place a topic on the weak-signal 2x2 (Park & Kim / WISDOM).

    x = average topic proportion (how much of the corpus it is)
    y = growth rate

        low volume,  high growth -> weak     (the horizon-scanning target)
        high volume, high growth -> strong   (already visible to everyone)
        high volume, low growth  -> latent   (established, not moving)
        low volume,  low growth  -> noise
    """
    high_volume = avg_proportion >= proportion_cut
    high_growth = growth >= growth_cut
    if high_growth:
        return "strong" if high_volume else "weak"
    return "latent" if high_volume else "noise"


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------


def run(config: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    """Execute Stage 2 and persist topics."""
    conn = db.init_db(resolve_path(config, "storage", "duckdb_path"))
    entry_id = db.log_stage_start(conn, run_id, STAGE, snapshot_config(config))
    try:
        result = _run_inner(conn, config, run_id)
        db.log_stage_finish(
            conn, entry_id, "success",
            records_in=result["documents"], records_out=len(result["topics"]),
            message=result["message"],
        )
        logger.info("Stage 2 complete: %s", result["message"])
        return result["topics"]
    except Exception as exc:
        db.log_stage_finish(conn, entry_id, "failed", message=str(exc))
        raise
    finally:
        conn.close()


def _run_inner(conn: Any, config: dict[str, Any], run_id: str) -> dict[str, Any]:
    documents = db.fetch_documents(conn)
    min_docs = int(get(config, "emergence", "min_docs_per_topic", default=20))
    min_topic_size = int(get(config, "emergence", "topics", "min_topic_size", default=8))

    if len(documents) < min_topic_size * 2:
        raise insufficient_data_error(
            STAGE,
            f"{len(documents)} documents collected; need at least {min_topic_size * 2}. "
            "Run Stage 1 first.",
        )

    all_slices = corpus_slices(documents)
    if len(all_slices) < 2:
        raise insufficient_data_error(
            STAGE,
            f"corpus spans {len(all_slices)} time slice(s); emergence detection needs "
            "at least 2 and is only meaningful with 4 or more.",
        )

    texts = [document_text(d) for d in documents]

    # --- embed ------------------------------------------------------------
    embedder = build_embedder(config)
    embedder.fit(texts)
    vectors = encode_with_cache(
        embedder, texts, conn,
        enabled=bool(get(config, "embeddings", "cache_vectors", default=True)),
    )
    logger.info(
        "Embedded %d documents with backend=%s (dim=%d)",
        len(texts), embedder.name, embedder.dimensions,
    )

    # --- cluster ----------------------------------------------------------
    method = str(get(config, "emergence", "topics", "method", default="agglomerative"))
    if method == "bertopic":
        found = topics_mod.cluster_bertopic(texts, vectors, min_topic_size=min_topic_size)
    else:
        found = topics_mod.cluster_agglomerative(
            vectors,
            threshold=topic_similarity_threshold(config),
            min_topic_size=min_topic_size,
            max_topics=int(get(config, "emergence", "topics", "max_topics", default=120)),
        )
    if not found:
        raise insufficient_data_error(
            STAGE,
            "clustering produced no topics — the similarity threshold for this backend "
            "is probably too high for the corpus. See emergence.topics."
            "similarity_threshold_by_backend.",
        )
    topics_mod.label_topics(found, texts)

    # --- per-topic scoring ------------------------------------------------
    totals = slice_totals(documents)
    totals_series = [totals.get(s, 0) for s in all_slices]

    # Novelty baseline: the corpus as it stood in its first third.
    early_cut = max(1, len(all_slices) // 3)
    early_slices = set(all_slices[:early_cut])
    early_indices = [
        i for i, d in enumerate(documents) if d.get("time_slice") in early_slices
    ]
    early_centroid = (
        centroid(vectors[early_indices]) if early_indices else np.zeros(vectors.shape[1])
    )

    percentiles = citation_percentiles(documents)
    weights = get(config, "emergence", "rotolo_weights", default={}) or {}
    burst_cfg = get(config, "emergence", "burst", default={}) or {}
    min_slices_growth = int(get(config, "emergence", "min_slices_for_growth", default=4))

    records: list[dict[str, Any]] = []
    for topic in found:
        member_docs = [documents[i] for i in topic.member_indices]
        member_vectors = vectors[topic.member_indices]
        series = topic_series(member_docs, all_slices)

        burst = detect_bursts(
            series, totals_series,
            s=float(burst_cfg.get("s", 2.0)),
            gamma=float(burst_cfg.get("gamma", 1.0)),
        )
        cagr = compute_cagr(series, min_slices=min_slices_growth)
        maturity, fit_quality = fit_logistic_maturity(series)

        # Growth attribute blends sustained trend (CAGR) with the burst signal,
        # so a topic that grew steadily and one that spiked both register, and
        # a topic doing both scores highest.
        growth_attr = float(
            np.clip(0.6 * _squash_cagr(cagr) + 0.4 * burst_score(burst), 0.0, 1.0)
        )

        attributes = {
            "novelty": compute_novelty(topic.centroid, early_centroid),
            "growth": growth_attr,
            "coherence": compute_coherence(member_vectors, topic.centroid),
            "impact": compute_impact(member_docs, percentiles),
            "uncertainty": compute_uncertainty(member_docs),
        }
        emergence = float(sum(attributes[k] * float(weights.get(k, 0.0)) for k in attributes))

        proportions = [
            (count / totals_series[i]) if totals_series[i] else 0.0
            for i, count in enumerate(series)
        ]
        avg_proportion = float(np.mean(proportions)) if proportions else 0.0
        present = [s for s, c in zip(all_slices, series) if c > 0]

        records.append(
            {
                "topic_id": topic.topic_id,
                "label": topic.label,
                "terms": topic.terms,
                "document_count": topic.size,
                "first_slice": present[0] if present else None,
                "last_slice": present[-1] if present else None,
                **attributes,
                "emergence_score": emergence,
                "burst_weight": burst.max_weight,
                "burst_slices": [all_slices[i] for i in burst.burst_indices],
                "cagr": cagr,
                "maturity": maturity,
                "horizon": assign_horizon(maturity, config),
                "avg_proportion": avg_proportion,
                "_fit_quality": fit_quality,
                "_growth_raw": cagr,
                "documents": [
                    {
                        "doc_id": documents[i]["doc_id"],
                        "similarity": float(vectors[i] @ topic.centroid),
                    }
                    for i in topic.member_indices
                ],
                "timeseries": [
                    {
                        "time_slice": slice_name,
                        "doc_count": count,
                        "proportion": proportions[i],
                        "in_burst": burst.states[i] == 1 if i < len(burst.states) else False,
                    }
                    for i, (slice_name, count) in enumerate(zip(all_slices, series))
                ],
            }
        )

    # --- signal classification (needs the full population) ----------------
    ws = get(config, "emergence", "weak_signal", default={}) or {}
    proportion_cut = float(
        np.percentile(
            [r["avg_proportion"] for r in records],
            float(ws.get("proportion_percentile_split", 50)),
        )
    )
    growth_cut = float(
        np.percentile(
            [r["growth"] for r in records], float(ws.get("growth_percentile_split", 50))
        )
    )
    for record in records:
        record["signal_class"] = classify_signal(
            record["avg_proportion"], record["growth"], proportion_cut, growth_cut
        )

    thin = sum(1 for r in records if r["document_count"] < min_docs)
    db.replace_topics(conn, run_id, records)

    message = (
        f"{len(records)} topics from {len(documents)} documents across "
        f"{len(all_slices)} slices ({all_slices[0]}-{all_slices[-1]}); "
        f"{sum(r['signal_class'] == 'weak' for r in records)} weak, "
        f"{sum(r['signal_class'] == 'strong' for r in records)} strong, "
        f"{sum(r['horizon'] == 'H3' for r in records)} H3; "
        f"{thin} topics below min_docs_per_topic={min_docs}"
    )
    if thin:
        logger.warning(
            "%d topics have fewer than %d documents. Their burst and growth figures "
            "are unreliable — the burst literature treats ~20 documents as the floor. "
            "Treat them as prompts to look, not findings.",
            thin, min_docs,
        )
    return {"topics": records, "documents": len(documents), "message": message}


def _squash_cagr(cagr: float) -> float:
    """Map an unbounded CAGR onto [0, 1].

    A CAGR of 1.0 (doubling every slice) lands at 0.5; higher rates approach 1
    without ever reaching it, so a single explosive topic cannot dominate the
    scale for everything else.
    """
    if cagr <= 0:
        return 0.0
    return float(cagr / (cagr + 1.0))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 2 — detect emergence.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--top", type=int, default=15, help="Print the top N topics.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    config = load_config(args.config)
    records = run(config, args.run_id)

    print(f"\n{'topic':7s} {'n':>4s} {'emrg':>5s} {'nov':>4s} {'grw':>4s} "
          f"{'coh':>4s} {'imp':>4s} {'unc':>4s} {'hzn':>3s} {'signal':7s} label")
    for r in sorted(records, key=lambda x: -x["emergence_score"])[: args.top]:
        print(
            f"{r['topic_id']:7s} {r['document_count']:4d} {r['emergence_score']:5.3f} "
            f"{r['novelty']:4.2f} {r['growth']:4.2f} {r['coherence']:4.2f} "
            f"{r['impact']:4.2f} {r['uncertainty']:4.2f} {r['horizon']:3s} "
            f"{r['signal_class']:7s} {r['label'][:52]}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
