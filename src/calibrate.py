"""
src/calibrate.py — calibration helpers.

Tools for the questions `docs/runbook-calibration.md` asks you to answer with
evidence rather than intuition.

    python -m src.calibrate threshold          # sweep the clustering threshold
    python -m src.calibrate threshold --show-labels
    python -m src.calibrate attributes         # attribute ranges and influence
    python -m src.calibrate bertopic           # sweep BERTopic seeds and neighbourhoods
    python -m src.calibrate attachment         # where to put the attachment threshold
    python -m src.calibrate critical-tech      # sweep the DISR match threshold

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
from dataclasses import replace
from typing import Any

import numpy as np

from src import db
from src.collectors.base import document_text
from src.config import (
    bertopic_params,
    critical_tech_match_threshold,
    critical_tech_match_weights,
    get,
    load_config,
    resolve_path,
    topic_similarity_threshold,
)
from src.embeddings import build_embedder, encode_with_cache
from src.errors import insufficient_data_error
from src.normalise import percentile_rank
from src.topics import (
    cluster_agglomerative,
    cluster_bertopic,
    cluster_leader,
    label_topics,
)

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
    """Embed the corpus and return (vectors, texts, total) for forming sources.

    Reads through the SAME vector cache the stages read, which is not a
    performance nicety — it is what makes a sweep describe the pipeline.
    Calling `embedder.encode` directly here re-embedded at this module's own
    batch size while the pipeline read back cached vectors; the two agreed to a
    cosine of 0.9999998, and UMAP turned that ~3e-7 difference into 118 clusters
    against the pipeline's 124. That discrepancy is recorded twice in
    PROJECT_STATE.md (issues 16 and 20) as something to work around. It is
    cheaper to remove. See issue 32.
    """
    with db.get_connection(resolve_path(config, "storage", "duckdb_path")) as conn:
        documents = db.fetch_documents(conn)
        if not documents:
            raise insufficient_data_error("calibrate", "no documents. Run Stage 1 first.")

        texts = [document_text(d) for d in documents]
        embedder = build_embedder(config)
        embedder.fit(texts)
        vectors = encode_with_cache(
            embedder, texts, conn,
            enabled=bool(get(config, "embeddings", "cache_vectors", default=True)),
        )

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
    # Checked before the corpus is loaded: embedding thousands of documents and
    # then refusing would be a several-minute way to print an error message.
    method = str(get(config, "emergence", "topics", "method", default="agglomerative"))
    if method == "bertopic":
        raise insufficient_data_error(
            "calibrate",
            "emergence.topics.method is 'bertopic', which takes no clustering "
            "threshold — HDBSCAN works on density, not on a cosine cut-off. "
            "Sweeping one here would report a number the pipeline never reads.\n"
            "  python -m src.calibrate bertopic     # seeds and n_neighbors\n"
            "  python -m src.calibrate attachment   # the one threshold bertopic DOES use\n"
            "To sweep this anyway, set emergence.topics.method to agglomerative first.",
        )

    vectors, texts, total = _load_forming_corpus(config)
    n = len(vectors)
    active = topic_similarity_threshold(config)
    min_topic_size = int(get(config, "emergence", "topics", "min_topic_size", default=8))
    max_topics = int(get(config, "emergence", "topics", "max_topics", default=120))

    # Sweep the method the pipeline will actually run. Sweeping one method and
    # configuring another is the mistake this whole file exists to prevent —
    # so `bertopic` refuses here rather than quietly sweeping average linkage
    # and reporting a threshold that would never be used. HDBSCAN clusters on
    # density in UMAP space and takes no cosine cut-off at all.
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


def sweep_bertopic(
    config: dict[str, Any],
    seeds: list[int],
    neighbours: list[int],
    show_labels: bool = False,
) -> None:
    """Sweep BERTopic's seed and neighbourhood, and report the shape of each result.

    TWO DIFFERENT QUESTIONS, and the output separates them because they have
    different answers.

    * **Across `n_neighbors`** the result should change: it sets how much local
      versus global structure UMAP preserves, so it genuinely decides what the
      topics are. Pick from the topic count, the assigned share and the size of
      the largest cluster, exactly as for the agglomerative threshold — and
      read the labels before believing any of them.
    * **Across seeds** the result should change *as little as possible*. Every
      seed is equally defensible a priori, so a parameter set whose output
      swings between seeds is one whose topics are an artefact of the
      initialisation rather than of the corpus. The seed spread printed below
      is therefore a diagnostic, not a leaderboard: a small spread means the
      choice of seed does not matter much, which is the result you want before
      writing one into the config and calling the run reproducible.

    Whatever you choose, record the seed in
    `emergence.topics.bertopic.random_state` and the reasoning in
    PROJECT_STATE.md. An unrecorded seed makes a run unreproducible; a seed
    recorded without its sweep makes it unarguable.
    """
    vectors, texts, total = _load_forming_corpus(config)
    n = len(vectors)
    min_topic_size = int(get(config, "emergence", "topics", "min_topic_size", default=8))
    max_topics = int(get(config, "emergence", "topics", "max_topics", default=120))
    base = bertopic_params(config, min_topic_size)

    print(
        f"\nBERTopic sweep over {n} forming documents (of {total} in the corpus), "
        f"backend {get(config, 'embeddings', 'backend', default='hashing')!r}.\n"
        f"Base parameters: {base.describe()}\n"
        f"Active seed: {base.random_state}\n"
    )
    print(f"{'n_neigh':>7} {'seed':>6} {'topics':>7} {'assigned':>9} {'share':>7} "
          f"{'largest':>8} {'outliers':>9}")
    print("-" * 60)

    by_neighbour: dict[int, list[tuple[int, int, int]]] = {}
    for n_neighbors in neighbours:
        for seed in seeds:
            params = replace(base, random_state=seed, n_neighbors=n_neighbors)
            topics = cluster_bertopic(
                texts, vectors,
                min_topic_size=min_topic_size, max_topics=max_topics, params=params,
            )
            if not topics:
                print(f"{n_neighbors:7d} {seed:6d} {'0':>7s}   (nothing clustered)")
                continue
            assigned = sum(t.size for t in topics)
            largest = max(t.size for t in topics)
            by_neighbour.setdefault(n_neighbors, []).append(
                (seed, len(topics), assigned)
            )
            print(
                f"{n_neighbors:7d} {seed:6d} {len(topics):7d} {assigned:9d} "
                f"{assigned / n:7.1%} {largest / assigned:8.1%} {n - assigned:9d}"
            )
            if show_labels:
                label_topics(topics, texts)
                for topic in topics[:8]:
                    print(f"{'':>14} {topic.size:5d}  {topic.label}")

    # The seed-stability diagnostic. A parameter set worth using is one where
    # this spread is small: it means the topics are a property of the corpus
    # rather than of where UMAP happened to start.
    print("\nSeed stability (spread across seeds at each n_neighbors):")
    print(f"{'n_neigh':>7} {'topics':>18} {'assigned':>22}")
    print("-" * 50)
    for n_neighbors, rows in sorted(by_neighbour.items()):
        counts = [r[1] for r in rows]
        assigned = [r[2] for r in rows]
        spread = (max(counts) - min(counts)) / max(np.mean(counts), 1)
        print(
            f"{n_neighbors:7d}   {min(counts):4d}-{max(counts):<4d} "
            f"(±{spread:5.1%})   {min(assigned):5d}-{max(assigned):<5d} "
            f"(±{(max(assigned) - min(assigned)) / max(np.mean(assigned), 1):5.1%})"
        )

    print(
        "\nChoose n_neighbors on the topics/assigned/largest columns and the labels.\n"
        "Choose the seed from a set whose spread is small — then WRITE IT DOWN in\n"
        "emergence.topics.bertopic.random_state and PROJECT_STATE.md. A seed that is\n"
        "not recorded is a run that cannot be reproduced.\n"
    )


def report_attachment(config: dict[str, Any]) -> None:
    """Report where the attachment threshold should sit, from the actual cosines.

    Documents from sources outside `forming_sources` — GDELT, in practice — are
    attached to the nearest finished topic at `attachment_threshold_ratio` of
    the clustering threshold. Under `bertopic` that is the *only* thing the
    clustering threshold is used for, since HDBSCAN takes no cosine cut-off, so
    this is the measurement that sets it.

    The number to look at is the distribution of "cosine from a non-forming
    document to its nearest topic centroid". Too high and the attention
    component of the Stage 4 index goes empty; too low and every headline
    attaches to something and attention stops discriminating.
    """
    with db.get_connection(resolve_path(config, "storage", "duckdb_path")) as conn:
        documents = db.fetch_documents(conn)
        if not documents:
            raise insufficient_data_error("calibrate", "no documents. Run Stage 1 first.")

        # Through the cache, for the reason in `_load_forming_corpus`.
        texts = [document_text(d) for d in documents]
        embedder = build_embedder(config)
        embedder.fit(texts)
        vectors = encode_with_cache(
            embedder, texts, conn,
            enabled=bool(get(config, "embeddings", "cache_vectors", default=True)),
        )

    forming = set(get(config, "emergence", "topics", "forming_sources", default=[]) or [])
    forming_idx = [i for i, d in enumerate(documents) if d["source"] in forming]
    other_idx = [i for i, d in enumerate(documents) if d["source"] not in forming]
    if not other_idx:
        print("\nEvery source forms topics — nothing is attached. Nothing to calibrate.\n")
        return

    min_topic_size = int(get(config, "emergence", "topics", "min_topic_size", default=8))
    max_topics = int(get(config, "emergence", "topics", "max_topics", default=120))
    method = str(get(config, "emergence", "topics", "method", default="agglomerative"))
    if method == "bertopic":
        topics = cluster_bertopic(
            [texts[i] for i in forming_idx], vectors[forming_idx],
            min_topic_size=min_topic_size, max_topics=max_topics,
            params=bertopic_params(config, min_topic_size),
        )
    else:
        topics = cluster_agglomerative(
            vectors[forming_idx], threshold=topic_similarity_threshold(config),
            min_topic_size=min_topic_size, max_topics=max_topics,
        )
    if not topics:
        raise insufficient_data_error("calibrate", "clustering produced no topics.")

    centroids = np.asarray([t.centroid for t in topics], dtype=np.float64)
    best = (vectors[other_idx] @ centroids.T).max(axis=1)

    ratio = float(
        get(config, "emergence", "topics", "attachment_threshold_ratio", default=0.6)
    )
    active = topic_similarity_threshold(config)

    sources = sorted({documents[i]["source"] for i in other_idx})
    print(
        f"\nCosine from each non-forming document to its nearest topic centroid.\n"
        f"{len(other_idx)} documents from {', '.join(sources)}; "
        f"{len(topics)} topics from {len(forming_idx)} forming documents.\n"
        f"Backend {get(config, 'embeddings', 'backend', default='hashing')!r}, "
        f"method {method!r}.\n"
    )
    for label, value in (
        ("min", best.min()), ("10th", np.percentile(best, 10)),
        ("median", np.percentile(best, 50)), ("90th", np.percentile(best, 90)),
        ("max", best.max()), ("mean", best.mean()),
    ):
        print(f"  {label:>7}  {value:.3f}")

    print(
        f"\nActive: threshold {active:.2f} x ratio {ratio} = "
        f"{active * ratio:.3f} effective, attaching "
        f"{100.0 * float((best >= active * ratio).mean()):.0f}% of them.\n"
    )
    print(f"{'threshold':>10} {'effective':>10} {'attached':>10}")
    print("-" * 32)
    for candidate in (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90):
        effective = candidate * ratio
        print(
            f"{candidate:10.2f} {effective:10.3f} "
            f"{float((best >= effective).mean()):10.1%}"
        )
    print(
        "\nAim to attach most of the attention corpus without attaching all of it —\n"
        "a threshold that attaches 100% has stopped discriminating, and one that\n"
        "attaches almost nothing empties the Stage 4 attention component.\n"
    )


def report_critical_tech(config: dict[str, Any]) -> None:
    """Sweep the DISR critical-technology match threshold against a real run.

    THE NUMBER THIS SETS DECIDES WHETHER THE FLAG MEANS ANYTHING. It is a
    cut-off on a blend that is 70% a cosine, so its scale belongs to the
    embedding backend — and the value that worked under `hashing` did not
    survive the move to `bge`. Until 2026-08-31 it was a bare Python default
    of 0.25, and under the shipped default it matched 114 of 114 topics: a
    national-interest designation printed on every evidence card, meaning
    nothing, with the +0.10 bonus attached. See PROJECT_STATE.md issue 21.

    Scores are computed with `stage3_scoring.critical_technology_scores`, the
    same function the pipeline matches on, so a sweep cannot drift away from
    what it is calibrating.
    """
    from src.stage3_scoring import critical_technology_scores

    backend = str(get(config, "embeddings", "backend", default="hashing"))
    embedding_weight, lexicon_weight = critical_tech_match_weights(config)

    with db.get_connection(resolve_path(config, "storage", "duckdb_path")) as conn:
        runs = conn.execute(
            "SELECT DISTINCT run_id FROM topics ORDER BY run_id DESC"
        ).fetchall()
        if not runs:
            raise insufficient_data_error("calibrate", "no topics. Run Stage 2 first.")
        run_id = runs[0][0]
        topics = db.fetch_topics(conn, run_id)
        refs = [r for r in db.fetch_strategy_refs(conn) if r["ref_type"] == "critical_tech"]
        if not refs:
            raise insufficient_data_error(
                "calibrate", "no critical_tech references. Run Stage 0 first."
            )

        # Same representation Stage 3 scores: a topic is its label plus its
        # terms, not its member documents.
        documents = db.fetch_documents(conn)
        corpus = [document_text(d) for d in documents] + [r["text"] for r in refs]
        embedder = build_embedder(config)
        embedder.fit(corpus)
        cache = bool(get(config, "embeddings", "cache_vectors", default=True))
        ref_vectors = encode_with_cache(
            embedder, [r["text"] for r in refs], conn, enabled=cache
        )
        topic_vectors = encode_with_cache(
            embedder,
            [" ".join([t["label"] or ""] + [term for term, _ in t["terms"]]) for t in topics],
            conn,
            enabled=cache,
        )

    best_score, best_field = [], []
    for i, topic in enumerate(topics):
        scores = critical_technology_scores(
            topic_vectors[i],
            [(str(term), float(w)) for term, w in topic["terms"]],
            refs,
            ref_vectors,
            embedding_weight=embedding_weight,
            lexicon_weight=lexicon_weight,
        )
        best_score.append(max(scores))
        best_field.append(refs[int(np.argmax(scores))]["code"])
    best = np.asarray(best_score)

    active = critical_tech_match_threshold(config)
    print(
        f"\nRun {run_id} — {len(topics)} topics against {len(refs)} DISR fields.\n"
        f"Backend {backend!r}, blend {embedding_weight} x cosine + "
        f"{lexicon_weight} x lexicon.\n"
    )
    for label, value in (
        ("min", best.min()), ("10th", np.percentile(best, 10)),
        ("median", np.percentile(best, 50)), ("90th", np.percentile(best, 90)),
        ("max", best.max()), ("mean", best.mean()),
    ):
        print(f"  {label:>7}  {value:.3f}")

    if active is None:
        print(
            f"\nActive: NOT SET for backend {backend!r} — no topic is matched and no "
            f"topic receives the critical_tech_bonus.\n"
        )
    else:
        print(
            f"\nActive: {active:.2f}, matching "
            f"{100.0 * float((best >= active).mean()):.0f}% of topics.\n"
        )

    print(f"{'threshold':>10} {'matched':>9} {'% of topics':>12}   distinct fields")
    print("-" * 62)
    for candidate in np.arange(0.10, 0.91, 0.05):
        hits = best >= candidate
        fields = {f for f, hit in zip(best_field, hits) if hit}
        marker = "  <- active" if active is not None and abs(candidate - active) < 1e-9 else ""
        print(
            f"{candidate:10.2f} {int(hits.sum()):9d} {hits.mean():11.0%}   "
            f"{len(fields):>2d} of {len(refs)}{marker}"
        )

    print(
        "\nWhat to look for. The DISR list is seven fields; a horizon scan over a\n"
        "broad frame should put a MINORITY of its topics inside one. A threshold\n"
        "matching ~100% has stopped discriminating and is worse than none, because\n"
        "it prints a policy designation on every evidence card. One matching ~0%\n"
        "means the blend never clears it and the bonus is dead weight. Read the\n"
        "labels of what matches at your candidate before settling.\n"
        "\nRecord the value and the reasoning in PROJECT_STATE.md's calibration log,\n"
        "then set scoring.strategic_fit.critical_tech_match.thresholds."
        f"{backend}.\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibration helpers.")
    parser.add_argument(
        "what",
        choices=["threshold", "attributes", "bertopic", "attachment", "critical-tech"],
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--values", default="", help="Comma-separated thresholds to sweep.")
    parser.add_argument("--show-labels", action="store_true",
                        help="Print topic labels at each threshold — read these before deciding.")
    parser.add_argument("--seeds", default="0,1,7,42,1234",
                        help="bertopic: comma-separated UMAP seeds to compare.")
    parser.add_argument("--n-neighbors", default="10,15,30",
                        help="bertopic: comma-separated UMAP n_neighbors to sweep.")
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
    elif args.what == "bertopic":
        sweep_bertopic(
            config,
            seeds=[int(v) for v in args.seeds.split(",") if v.strip()],
            neighbours=[int(v) for v in args.n_neighbors.split(",") if v.strip()],
            show_labels=args.show_labels,
        )
    elif args.what == "attachment":
        report_attachment(config)
    elif args.what == "critical-tech":
        report_critical_tech(config)
    else:
        report_attributes(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
