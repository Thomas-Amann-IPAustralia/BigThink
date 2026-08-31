"""
src/calibrate.py — calibration helpers.

Tools for the questions `docs/runbook-calibration.md` asks you to answer with
evidence rather than intuition.

    python -m src.calibrate threshold          # sweep the clustering threshold
    python -m src.calibrate threshold --show-labels
    python -m src.calibrate attributes         # attribute ranges and influence

WHY THIS EXISTS. The clustering threshold is the single most consequential
number in the pipeline and it cannot be reasoned to — it depends on the corpus,
the scan frame and the embedding backend. Set it too low and one mega-cluster
absorbs most of the corpus and labels itself with the vocabulary of the whole
domain; set it too high and everything fragments into singletons. The sweep
finds the inflection, and the inflection is usually obvious once you can see it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

import numpy as np

from src import db
from src.collectors.base import document_text
from src.config import get, load_config, resolve_path, topic_similarity_threshold
from src.embeddings import build_embedder
from src.errors import insufficient_data_error
from src.normalise import percentile_rank
from src.topics import cluster_agglomerative, cluster_leader, label_topics

logger = logging.getLogger(__name__)

#: Sweep ranges are per method, because a threshold means a different thing
#: under each: `leader` compares a document to a centroid, `agglomerative` the
#: mean pairwise similarity between two clusters' members. Measured on 2,987
#: real OpenAlex documents under `hashing`, mean pairwise cosine was 0.075 and
#: the 99th percentile 0.191 — so a sweep starting at 0.14 would show
#: `agglomerative` nothing but its collapse.
DEFAULT_SWEEP_BY_METHOD = {
    "agglomerative": [0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.24],
    "leader": [0.14, 0.18, 0.22, 0.26, 0.28, 0.30, 0.34, 0.38, 0.42, 0.50, 0.60, 0.70],
}
DEFAULT_SWEEP = DEFAULT_SWEEP_BY_METHOD["agglomerative"]


def _load_forming_corpus(config: dict[str, Any]) -> tuple[np.ndarray, list[str], int]:
    """Embed the corpus and return (vectors, texts, total) for forming sources."""
    with db.get_connection(resolve_path(config, "storage", "duckdb_path")) as conn:
        documents = db.fetch_documents(conn)
    if not documents:
        raise insufficient_data_error("calibrate", "no documents. Run Stage 1 first.")

    texts = [document_text(d) for d in documents]
    embedder = build_embedder(config)
    embedder.fit(texts)
    vectors = embedder.encode(texts)

    forming = set(get(config, "emergence", "topics", "forming_sources", default=[]) or [])
    indices = [
        i for i, d in enumerate(documents)
        if not forming or d["source"] in forming
    ]
    return vectors[indices], [texts[i] for i in indices], len(documents)


def sweep_threshold(
    config: dict[str, Any], values: list[float], show_labels: bool = False
) -> None:
    """Cluster at each threshold and report the shape of the result."""
    vectors, texts, total = _load_forming_corpus(config)
    n = len(vectors)
    active = topic_similarity_threshold(config)
    method = str(get(config, "emergence", "topics", "method", default="agglomerative"))
    min_topic_size = int(get(config, "emergence", "topics", "min_topic_size", default=8))
    max_topics = int(get(config, "emergence", "topics", "max_topics", default=120))

    # Sweep the method the pipeline will actually run. Sweeping one method and
    # configuring another is the mistake this whole file exists to prevent.
    cluster = cluster_leader if method == "leader" else cluster_agglomerative

    print(
        f"\nCorpus: {total:,} documents, {n:,} from topic-forming sources.\n"
        f"Method: {method}  ·  Backend: "
        f"{get(config, 'embeddings', 'backend', default='hashing')} "
        f"(active threshold {active}).\n"
    )
    print(f"{'thresh':>7s} {'topics':>7s} {'assigned':>9s} {'assign%':>8s} "
          f"{'largest':>8s} {'largest%':>9s} {'median':>7s}")
    print("-" * 62)

    results = []
    for threshold in values:
        topics = cluster(
            vectors, threshold=threshold,
            min_topic_size=min_topic_size, max_topics=max_topics,
        )
        if not topics:
            print(f"{threshold:7.2f} {'0':>7s}   (nothing clustered)")
            continue
        sizes = sorted((t.size for t in topics), reverse=True)
        assigned = sum(sizes)
        largest_share = sizes[0] / n
        results.append((threshold, len(topics), assigned, largest_share))
        marker = "  <- active" if abs(threshold - active) < 1e-9 else ""
        print(
            f"{threshold:7.2f} {len(topics):7d} {assigned:9d} {assigned / n:7.1%} "
            f"{sizes[0]:8d} {largest_share:8.1%} {int(np.median(sizes)):7d}{marker}"
        )
        if show_labels:
            label_topics(topics, texts)
            for topic in topics[:12]:
                print(f"            n={topic.size:5d}  {topic.label}")
            print()

    # Point at the inflection: the first threshold where no single cluster
    # dominates. That is almost always the right neighbourhood.
    healthy = [r for r in results if r[3] < 0.25]
    print()
    if healthy:
        threshold, topics_n, assigned, share = healthy[0]
        print(
            f"First threshold where no cluster exceeds 25% of the corpus: "
            f"{threshold:.2f} ({topics_n} topics, {assigned / n:.0%} of documents "
            f"assigned, largest {share:.0%}).\n"
            "Below this a single mega-cluster absorbs the corpus and labels itself\n"
            "with the vocabulary of the whole domain. Above it, topics fragment and\n"
            "the share of assigned documents falls. Read the labels before deciding."
        )
    else:
        print(
            "No threshold in this sweep broke up the largest cluster below 25%.\n"
            "Extend the sweep upward with --values, or check whether the scan frame\n"
            "is so narrow that the corpus genuinely is one topic."
        )
    print(
        "\nRecord whatever you choose, and why, in PROJECT_STATE.md — a threshold\n"
        "with no recorded reason is indistinguishable from a bug six months on.\n"
    )


def report_attributes(config: dict[str, Any]) -> None:
    """Show each Rotolo attribute's spread and its influence on the ranking.

    Attributes are rank-normalised before weighting precisely so that influence
    matches the configured weight. This report is the check that it does.
    """
    run_id = str(get(config, "pipeline", "run_label", default=""))
    with db.get_connection(resolve_path(config, "storage", "duckdb_path")) as conn:
        runs = conn.execute(
            "SELECT DISTINCT run_id FROM topics ORDER BY run_id DESC"
        ).fetchall()
        if not runs:
            raise insufficient_data_error("calibrate", "no topics. Run Stage 2 first.")
        run_id = runs[0][0]
        topics = db.fetch_topics(conn, run_id)

    names = ("novelty", "growth", "coherence", "impact", "uncertainty")
    weights = get(config, "emergence", "rotolo_weights", default={}) or {}
    raw = {name: [float(t[name] or 0.0) for t in topics] for name in names}
    ranked = {name: percentile_rank(values) for name, values in raw.items()}

    print(f"\nRun {run_id} — {len(topics)} topics\n")
    print(f"{'attribute':13s} {'weight':>7s} {'raw min':>8s} {'raw max':>8s} "
          f"{'raw sd':>7s} {'influence':>10s}")
    print("-" * 60)
    denominator = sum(
        float(weights.get(name, 0.0)) * float(np.std(ranked[name])) for name in names
    ) or 1.0
    for name in names:
        influence = float(weights.get(name, 0.0)) * float(np.std(ranked[name])) / denominator
        print(
            f"{name:13s} {float(weights.get(name, 0.0)):7.2f} "
            f"{min(raw[name]):8.2f} {max(raw[name]):8.2f} {np.std(raw[name]):7.3f} "
            f"{influence:9.1%}"
        )
    print(
        "\nInfluence should track the configured weight closely — that is what\n"
        "rank-normalisation buys. A large gap means the normalisation is not\n"
        "running, or an attribute is constant across every topic (check raw sd).\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibration helpers.")
    parser.add_argument("what", choices=["threshold", "attributes"])
    parser.add_argument("--config", default=None)
    parser.add_argument("--values", default="", help="Comma-separated thresholds to sweep.")
    parser.add_argument("--show-labels", action="store_true",
                        help="Print topic labels at each threshold — read these before deciding.")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)-7s %(message)s")
    config = load_config(args.config)

    if args.what == "threshold":
        method = str(get(config, "emergence", "topics", "method", default="agglomerative"))
        values = (
            [float(v) for v in args.values.split(",") if v.strip()]
            if args.values
            else DEFAULT_SWEEP_BY_METHOD.get(method, DEFAULT_SWEEP)
        )
        sweep_threshold(config, values, show_labels=args.show_labels)
    else:
        report_attributes(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
