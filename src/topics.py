"""
src/topics.py — topic formation and labelling.

Groups documents into topics and gives each a readable label. Two methods:

  agglomerative  Deterministic centroid clustering over the embedding space,
                 numpy only. Single leader pass, then one reassignment pass,
                 then a merge pass. Default.

  bertopic       Delegates to BERTopic when requirements-ml.txt is installed.
                 Better topics, heavy dependency, non-deterministic unless UMAP
                 is seeded.

WHY NOT JUST BERTOPIC

The proposal names BERTopic, and it is the right destination. But BERTopic
brings UMAP and HDBSCAN, whose output shifts between runs unless carefully
seeded — and this pipeline's whole value is that a score can be compared with
last month's. The agglomerative method is worse at finding topics and perfect
at reproducing them, which is the right trade while the method is being
calibrated. Switch once the weights are settled.

LABELLING

Labels come from c-TF-IDF, the same idea BERTopic uses: treat each topic as one
concatenated document, score terms by term frequency within the topic against
inverse frequency across topics. The result is the terms that distinguish this
topic from its neighbours, rather than the terms that are merely common in it.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from src.embeddings import normalise_tokens

logger = logging.getLogger(__name__)

_MAX_LABEL_TERMS = 8


@dataclass
class Topic:
    """A cluster of documents plus its derived label."""

    topic_id: str
    member_indices: list[int] = field(default_factory=list)
    centroid: np.ndarray | None = None
    terms: list[tuple[str, float]] = field(default_factory=list)
    label: str = ""

    @property
    def size(self) -> int:
        return len(self.member_indices)


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def cluster_agglomerative(
    vectors: np.ndarray,
    *,
    threshold: float,
    min_topic_size: int,
    max_topics: int,
    merge_threshold: float | None = None,
) -> list[Topic]:
    """Cluster L2-normalised row vectors into topics.

    Three passes:
      1. Leader pass — assign each document to the nearest centroid above
         `threshold`, else start a new cluster. O(n*k).
      2. Reassignment pass — with all centroids known, move each document to
         its best one. Removes the order-dependence of the leader pass.
      3. Merge pass — combine clusters whose centroids exceed
         `merge_threshold`, which defaults to a little above `threshold`.

    Documents below `threshold` against every centroid after the merge pass end
    up in clusters that fall under `min_topic_size` and are dropped. Dropping
    is deliberate: an unassignable document is noise for this purpose, and
    forcing it into the nearest topic would corrupt that topic's centroid.
    """
    n = len(vectors)
    if n == 0:
        return []
    if merge_threshold is None:
        merge_threshold = min(threshold + 0.15, 0.95)

    # --- pass 1: leader ---------------------------------------------------
    centroids: list[np.ndarray] = []
    sums: list[np.ndarray] = []
    assignments = np.full(n, -1, dtype=int)

    for i in range(n):
        vec = vectors[i]
        if not np.any(vec):  # empty text
            continue
        if centroids:
            sims = np.asarray(centroids) @ vec
            best = int(np.argmax(sims))
            if sims[best] >= threshold:
                assignments[i] = best
                sums[best] = sums[best] + vec
                centroids[best] = _normalise(sums[best])
                continue
        if len(centroids) >= max_topics:
            continue  # cap reached; this document stays unassigned
        centroids.append(vec.copy())
        sums.append(vec.copy())
        assignments[i] = len(centroids) - 1

    if not centroids:
        return []

    # --- pass 2: reassignment --------------------------------------------
    centroid_matrix = np.asarray(centroids)
    sims = vectors @ centroid_matrix.T           # (n, k)
    best_idx = np.argmax(sims, axis=1)
    best_sim = sims[np.arange(n), best_idx]
    assignments = np.where(best_sim >= threshold, best_idx, -1)

    centroids = _recompute_centroids(vectors, assignments, len(centroid_matrix))

    # --- pass 3: merge -----------------------------------------------------
    assignments, centroids = _merge_similar(vectors, assignments, centroids, merge_threshold)

    # --- assemble ----------------------------------------------------------
    topics: list[Topic] = []
    for cluster in range(len(centroids)):
        members = np.flatnonzero(assignments == cluster).tolist()
        if len(members) < min_topic_size:
            continue
        topics.append(
            Topic(topic_id="", member_indices=members, centroid=centroids[cluster])
        )

    # Largest first, then a stable tiebreak so ids are reproducible.
    topics.sort(key=lambda t: (-t.size, t.member_indices[0]))
    for rank, topic in enumerate(topics):
        topic.topic_id = f"T{rank:04d}"
    logger.info(
        "Clustered %d documents into %d topics (>= %d members) at threshold %.2f",
        n, len(topics), min_topic_size, threshold,
    )
    return topics


def _normalise(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


def _recompute_centroids(
    vectors: np.ndarray, assignments: np.ndarray, k: int
) -> list[np.ndarray]:
    centroids = []
    for cluster in range(k):
        members = vectors[assignments == cluster]
        centroids.append(
            _normalise(members.mean(axis=0)) if len(members) else np.zeros(vectors.shape[1])
        )
    return centroids


def _merge_similar(
    vectors: np.ndarray,
    assignments: np.ndarray,
    centroids: list[np.ndarray],
    merge_threshold: float,
) -> tuple[np.ndarray, list[np.ndarray]]:
    """Union-find merge of clusters whose centroids are near-duplicates."""
    k = len(centroids)
    if k < 2:
        return assignments, centroids

    parent = list(range(k))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    matrix = np.asarray(centroids)
    sims = matrix @ matrix.T
    for a in range(k):
        for b in range(a + 1, k):
            if sims[a, b] >= merge_threshold:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)

    # Renumber surviving clusters contiguously.
    roots = sorted({find(c) for c in range(k)})
    remap = {root: i for i, root in enumerate(roots)}
    new_assignments = np.array(
        [remap[find(a)] if a >= 0 else -1 for a in assignments], dtype=int
    )
    if len(roots) < k:
        logger.info("Merged %d near-duplicate clusters", k - len(roots))
    return new_assignments, _recompute_centroids(vectors, new_assignments, len(roots))


# ---------------------------------------------------------------------------
# Labelling (c-TF-IDF)
# ---------------------------------------------------------------------------


def label_topics(topics: Sequence[Topic], texts: Sequence[str]) -> None:
    """Assign `terms` and `label` to each topic, in place, via c-TF-IDF."""
    if not topics:
        return

    topic_counts: list[Counter[str]] = []
    for topic in topics:
        counter: Counter[str] = Counter()
        for idx in topic.member_indices:
            counter.update(_phrases(texts[idx]))
        topic_counts.append(counter)

    # How many topics contain each term at all.
    topic_frequency: Counter[str] = Counter()
    for counter in topic_counts:
        topic_frequency.update(counter.keys())

    n_topics = len(topics)
    for topic, counter in zip(topics, topic_counts):
        total = sum(counter.values()) or 1
        scored: list[tuple[str, float]] = []
        for term, count in counter.items():
            if count < 2:
                continue  # a term used once cannot characterise a topic
            tf = count / total
            idf = math.log(1.0 + n_topics / (1 + topic_frequency[term]))
            scored.append((term, tf * idf))
        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        topic.terms = [(t, round(w, 6)) for t, w in scored[:_MAX_LABEL_TERMS]]
        topic.label = _compose_label(topic.terms)


def _phrases(text: str) -> list[str]:
    """Unigrams plus bigrams, stopword-filtered.

    Bigrams matter here: 'quantum error correction' is a topic, 'quantum' and
    'error' separately are not.
    """
    tokens = normalise_tokens(text)
    return tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]


def _compose_label(terms: Sequence[tuple[str, float]]) -> str:
    """Build a readable label, dropping terms subsumed by a higher-ranked one.

    Without this, labels read 'patent, patent examination, examination' —
    technically the top terms, useless to a reader.
    """
    chosen: list[str] = []
    for term, _ in terms:
        if any(term in existing or existing in term for existing in chosen):
            continue
        chosen.append(term)
        if len(chosen) == 4:
            break
    return " / ".join(chosen)


# ---------------------------------------------------------------------------
# BERTopic delegation
# ---------------------------------------------------------------------------


def cluster_bertopic(
    texts: Sequence[str],
    vectors: np.ndarray,
    *,
    min_topic_size: int,
    random_state: int = 42,
) -> list[Topic]:
    """Cluster with BERTopic, reusing embeddings already computed here.

    Seeds UMAP so runs are reproducible. BERTopic's -1 outlier cluster is
    dropped rather than kept, matching the agglomerative method's treatment of
    unassignable documents.
    """
    try:
        from bertopic import BERTopic  # noqa: PLC0415
        from umap import UMAP  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ImportError(
            "emergence.topics.method='bertopic' requires bertopic and umap-learn.\n"
            "  pip install -r requirements-ml.txt"
        ) from exc

    model = BERTopic(
        min_topic_size=min_topic_size,
        umap_model=UMAP(
            n_neighbors=15, n_components=5, min_dist=0.0,
            metric="cosine", random_state=random_state,
        ),
        calculate_probabilities=False,
        verbose=False,
    )
    assignments, _ = model.fit_transform(list(texts), embeddings=vectors)

    topics: list[Topic] = []
    for cluster in sorted({int(a) for a in assignments if int(a) >= 0}):
        members = [i for i, a in enumerate(assignments) if int(a) == cluster]
        if len(members) < min_topic_size:
            continue
        topics.append(
            Topic(
                topic_id="",
                member_indices=members,
                centroid=_normalise(vectors[members].mean(axis=0)),
                terms=[(w, float(s)) for w, s in (model.get_topic(cluster) or [])[:_MAX_LABEL_TERMS]],
            )
        )
    topics.sort(key=lambda t: (-t.size, t.member_indices[0]))
    for rank, topic in enumerate(topics):
        topic.topic_id = f"T{rank:04d}"
        topic.label = _compose_label(topic.terms)
    return topics
