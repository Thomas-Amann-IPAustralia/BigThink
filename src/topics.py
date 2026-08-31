"""
src/topics.py — topic formation and labelling.

Groups documents into topics and gives each a readable label. Three methods:

  bertopic       BERTopic over an explicitly seeded UMAP + HDBSCAN pair.
                 Requires requirements-ml.txt. The default since 2026-08-31.

  agglomerative  True average-linkage agglomerative clustering over cosine
                 similarity, numpy only. Order-invariant. The default from
                 2026-08-30 to 2026-08-31, and still the fallback whenever the
                 ML dependencies are not installed.

  leader         Sequential nearest-centroid ("leader") clustering, then one
                 reassignment pass, then a merge pass. The pre-2026-08-30
                 default, kept so an old run can be reproduced from its config
                 snapshot. See the warning below before choosing it.

WHY `leader` WAS REPLACED (measured on the 2026-08-30 run)

`leader` updates a cluster's centroid in place as it accretes, which creates a
feedback loop: a growing cluster's centroid drifts toward the corpus mean, a
mean-ward centroid resembles everything, so it absorbs more. The 2026-08-30 run
recorded the end state of that loop. Its largest topic held 1,497 documents —
57% of everything assigned — under the incoherent label "image / patent /
learning / watermark", and its stored novelty of 0.045 means its centroid sat
at cosine 0.955 from the corpus centroid, against 0.12-0.43 for every other
topic. It was not a large topic. It was the corpus mean with a label on it.

Two aggravating factors, both now gone by construction:

  * Documents arrive `ORDER BY published_date`, so clusters were seeded by the
    oldest documents and spent eight years accreting. A topic first appearing
    in 2024 had to out-compete centroids that had already absorbed everything
    before it — backwards, in an instrument built to find the new.
  * On reaching `max_topics` the leader pass silently dropped every later
    unmatched document, and under chronological ordering those were the recent
    ones.

Average linkage compares a candidate against the *mean pairwise similarity* to
a cluster's members rather than against its centroid, so a bloated cluster
becomes progressively harder to join instead of easier. It is also
order-invariant, so the seeding bias cannot exist — pinned by
`test_average_linkage_is_order_invariant`.

WHY BERTOPIC IS NOW THE DEFAULT (decided 2026-08-31)

This module used to argue against it: BERTopic brings UMAP and HDBSCAN, whose
output shifts between runs unless carefully seeded, and the pipeline's value
was taken to be that a score could be compared with last month's.

That trade has been made the other way round, on the owner's instruction. A
single scan should be as accurate and as useful *on its own* as it can be, and
its value as a reference point for a future run is explicitly secondary. Every
argument for average linkage over BERTopic was an argument about the second
thing.

Two halves of "shifts between runs", worth separating because only one of them
is now accepted:

  * **Within a corpus** it does not shift. UMAP is seeded from
    `BertopicParams.random_state` and HDBSCAN is deterministic, so the same
    corpus and seed give the same topics — pinned by
    `test_bertopic_is_deterministic_under_a_fixed_seed`. This is the property
    that makes a result checkable, and it is kept.
  * **Across corpora** it does shift, and more than average linkage does. UMAP
    fits a manifold to the whole corpus, so next week's documents move this
    week's topics rather than merely adding to them. That is the cost, it is
    accepted deliberately, and it is why `docs/method.md` says a topic id means
    nothing across runs.

The bake-off harness that would have settled the original argument on evidence
was never built, so the old default rested on the same kind of reasoning it
warned against. What replaced it is a measurement: see the calibration log
entry for 2026-08-31 in PROJECT_STATE.md, and `python -m src.calibrate
bertopic` for the sweep that produced it.

LABELLING

Labels come from c-TF-IDF, the same idea BERTopic uses: treat each topic as one
concatenated document, score terms by term frequency within the topic against
inverse frequency across topics. The result is the terms that distinguish this
topic from its neighbours, rather than the terms that are merely common in it.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from src.embeddings import normalise_tokens

logger = logging.getLogger(__name__)

# Two different jobs, two different lengths.
#   Labels are read by people: four distinct terms, deduplicated.
#   Scoring matches a topic against strategy and asset lexicons, which are
#   10-12 multi-word entries each. With only eight terms — several of them
#   near-duplicates like "geographical indication" / "indication" /
#   "geographical" — almost no lexicon entry can match, and the whole
#   asset-leverage axis collapses to near zero for every topic.
_MAX_SCORING_TERMS = 30
_MAX_LABEL_TERMS = 4


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


#: Guard on the pairwise similarity matrix. n=12,000 is ~576 MB at float32,
#: which fits a GitHub Actions runner; beyond that the operator should say so
#: deliberately rather than discover it as an OOM kill halfway through a scan.
_MAX_PAIRWISE = 12_000


def cluster_agglomerative(
    vectors: np.ndarray,
    *,
    threshold: float,
    min_topic_size: int,
    max_topics: int,
    max_pairwise: int = _MAX_PAIRWISE,
) -> list[Topic]:
    """Average-linkage agglomerative clustering over cosine similarity.

    Repeatedly merges the two clusters with the highest mean pairwise
    similarity, stopping when the best remaining pair falls below `threshold`.
    Clusters smaller than `min_topic_size` are dropped rather than forced into
    a neighbour — an unassignable document is noise for this purpose, and
    attaching it would corrupt the topic it landed in.

    Two properties matter and neither is incidental:

    * **Order-invariant.** The result depends on the vectors, not the sequence
      they arrive in, so the chronological ordering of the corpus cannot bias
      which topics form. `cluster_leader` had exactly that bias.
    * **Resistant to mega-clusters.** Linkage is the mean similarity between
      two clusters' members, not the similarity to a centroid. A cluster that
      has absorbed a lot of unrelated material has a low mean similarity to
      anything new, so it becomes harder to join as it grows. Under
      `cluster_leader` the opposite was true.

    Similarities are updated with the Lance-Williams recurrence rather than
    recomputed from members, which is what keeps this O(n^2) rather than
    O(n^3): merging clusters a and b of sizes na, nb gives, for every other
    cluster x, ``sim(ab, x) = (na*sim(a,x) + nb*sim(b,x)) / (na + nb)``.
    """
    n = len(vectors)
    if n == 0:
        return []
    if n > max_pairwise:
        raise ValueError(
            f"agglomerative clustering needs an {n}x{n} similarity matrix "
            f"(~{n * n * 4 / 1e9:.1f} GB at float32), above the {max_pairwise} "
            "document guard. Raise emergence.topics.max_pairwise if the machine "
            "has the memory, or use method: leader."
        )

    # Documents with no usable text cannot cluster; excluding them up front
    # keeps them out of every centroid rather than dragging one toward zero.
    live = np.flatnonzero(np.any(vectors, axis=1))
    if len(live) < 2:
        return []
    v = np.asarray(vectors[live], dtype=np.float32)

    sims = v @ v.T
    np.fill_diagonal(sims, -np.inf)

    sizes = np.ones(len(live), dtype=np.float64)
    active = np.ones(len(live), dtype=bool)
    members: list[list[int]] = [[i] for i in range(len(live))]

    # Nearest-neighbour cache. Merging can only lower a similarity (the merged
    # value is a weighted mean of the two), so a cached value is an upper
    # bound: revalidating the current maximum before acting on it is enough to
    # keep the choice exact.
    nn_sim = sims.max(axis=1)
    nn_idx = sims.argmax(axis=1)

    while True:
        candidate = int(np.argmax(np.where(active, nn_sim, -np.inf)))
        best = nn_sim[candidate]
        if not np.isfinite(best) or best < threshold:
            break

        other = int(nn_idx[candidate])
        if not active[other]:
            row = np.where(active, sims[candidate], -np.inf)
            row[candidate] = -np.inf
            nn_idx[candidate] = int(np.argmax(row))
            nn_sim[candidate] = row[nn_idx[candidate]]
            continue

        fresh = float(sims[candidate, other])
        if fresh < best - 1e-9:
            nn_sim[candidate] = fresh
            continue

        a, b = (candidate, other) if candidate < other else (other, candidate)
        total = sizes[a] + sizes[b]
        sims[a] = (sizes[a] * sims[a] + sizes[b] * sims[b]) / total
        sims[:, a] = sims[a]
        sims[a, a] = -np.inf
        sims[b, :] = -np.inf
        sims[:, b] = -np.inf

        members[a].extend(members[b])
        members[b] = []
        sizes[a] = total
        active[b] = False

        row = np.where(active, sims[a], -np.inf)
        row[a] = -np.inf
        nn_idx[a] = int(np.argmax(row))
        nn_sim[a] = row[nn_idx[a]]
        nn_sim[b] = -np.inf

    topics: list[Topic] = []
    for cluster in np.flatnonzero(active):
        indices = [int(live[i]) for i in members[cluster]]
        if len(indices) < min_topic_size:
            continue
        topics.append(
            Topic(
                topic_id="",
                member_indices=sorted(indices),
                centroid=_normalise(np.asarray(vectors[indices], dtype=np.float64).mean(axis=0)),
            )
        )

    # Largest first, then a stable tiebreak so ids are reproducible.
    topics.sort(key=lambda t: (-t.size, t.member_indices[0]))
    if len(topics) > max_topics:
        logger.info(
            "Keeping the %d largest of %d topics (emergence.topics.max_topics)",
            max_topics, len(topics),
        )
        topics = topics[:max_topics]
    for rank, topic in enumerate(topics):
        topic.topic_id = f"T{rank:04d}"
    assigned = sum(t.size for t in topics)
    logger.info(
        "Agglomerated %d documents into %d topics (>= %d members) at threshold %.2f; "
        "%d documents (%.0f%%) assigned",
        n, len(topics), min_topic_size, threshold, assigned, 100.0 * assigned / max(n, 1),
    )
    return topics


def cluster_leader(
    vectors: np.ndarray,
    *,
    threshold: float,
    min_topic_size: int,
    max_topics: int,
    merge_threshold: float | None = None,
) -> list[Topic]:
    """Sequential nearest-centroid clustering. Superseded — see the module docstring.

    Retained only so a run collected before 2026-08-30 can be reproduced from
    its config snapshot. It produces a mega-cluster by construction; prefer
    `cluster_agglomerative`.

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
    # The centroid matrix is preallocated and updated in place. Rebuilding it
    # from a list on each document (np.asarray(centroids) inside the loop) is
    # the obvious way to write this and is quadratic in disguise: at 7,000
    # documents and 120 centroids it rebuilds a 120x2048 array 7,000 times and
    # turns a few seconds of work into many minutes.
    dimensions = vectors.shape[1]
    centroid_matrix = np.zeros((max_topics, dimensions), dtype=np.float64)
    sums = np.zeros((max_topics, dimensions), dtype=np.float64)
    n_clusters = 0
    assignments = np.full(n, -1, dtype=int)

    for i in range(n):
        vec = vectors[i]
        if not np.any(vec):  # empty text
            continue
        if n_clusters:
            sims = centroid_matrix[:n_clusters] @ vec
            best = int(np.argmax(sims))
            if sims[best] >= threshold:
                assignments[i] = best
                sums[best] += vec
                centroid_matrix[best] = _normalise(sums[best])
                continue
        if n_clusters >= max_topics:
            continue  # cap reached; this document stays unassigned
        centroid_matrix[n_clusters] = vec
        sums[n_clusters] = vec
        assignments[i] = n_clusters
        n_clusters += 1

    if not n_clusters:
        return []

    # --- pass 2: reassignment --------------------------------------------
    centroid_matrix = centroid_matrix[:n_clusters]
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
        "Leader-clustered %d documents into %d topics (>= %d members) at threshold %.2f",
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
        topic.terms = [(t, round(w, 6)) for t, w in scored[:_MAX_SCORING_TERMS]]
        topic.label = _compose_label(topic.terms)


def drop_vocabulary_poor_topics(
    topics: Sequence[Topic], min_distinct_terms: int = 3
) -> list[Topic]:
    """Discard topics with too little distinct vocabulary to be a theme.

    Must run after `label_topics`, since it reads `terms`.

    A cluster can pass the size threshold on documents that share one generic
    word — a run of data.gov.au dataset titles produced a topic whose entire
    vocabulary was "index", which then ranked eighth on the shortlist. A theme
    that cannot be described in three distinct terms is not a theme.
    """
    kept, dropped = [], []
    for topic in topics:
        distinct = {term for term, _ in topic.terms}
        (kept if len(distinct) >= min_distinct_terms else dropped).append(topic)
    if dropped:
        logger.info(
            "Dropped %d topic(s) with fewer than %d distinct terms: %s",
            len(dropped), min_distinct_terms,
            ", ".join(t.label or t.topic_id for t in dropped),
        )
    # Renumber so ids stay contiguous.
    for rank, topic in enumerate(kept):
        topic.topic_id = f"T{rank:04d}"
    return kept


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
        if len(chosen) == _MAX_LABEL_TERMS:
            break
    return " / ".join(chosen)


# ---------------------------------------------------------------------------
# BERTopic delegation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BertopicParams:
    """Every hyperparameter that decides a BERTopic result, in one record.

    Frozen and in one place because these are the numbers a reader has to be
    able to check. A BERTopic run is reproducible only as far as its seed and
    its neighbourhood parameters are written down, so `describe()` renders them
    into the run log and Stage 2 stores the same values in the config snapshot.

    `random_state` is load-bearing twice over. Unseeded, UMAP's stochastic
    initialisation moves documents between clusters on identical input, so two
    runs of the same corpus disagree about what the topics are. Seeded, it is
    deterministic — at the cost of single-threaded UMAP, which is where the
    "n_jobs overridden to 1" warning comes from and is a price worth paying.
    """

    random_state: int = 42
    # --- UMAP: the manifold the clusterer sees ---
    n_neighbors: int = 15        # local vs global structure
    n_components: int = 5        # dimensions HDBSCAN clusters in
    min_dist: float = 0.0        # 0.0 packs points tightly, which is what a
                                 # density clusterer wants downstream
    metric: str = "cosine"       # embeddings are L2-normalised
    # --- HDBSCAN: the clusters themselves ---
    min_cluster_size: int = 8    # defaults to min_topic_size at the call site
    min_samples: int | None = None   # None -> HDBSCAN uses min_cluster_size
    cluster_selection_method: str = "eom"

    def describe(self) -> str:
        return (
            f"seed={self.random_state} umap(n_neighbors={self.n_neighbors}, "
            f"n_components={self.n_components}, min_dist={self.min_dist}, "
            f"metric={self.metric}) hdbscan(min_cluster_size={self.min_cluster_size}, "
            f"min_samples={self.min_samples}, "
            f"cluster_selection_method={self.cluster_selection_method})"
        )


def cluster_bertopic(
    texts: Sequence[str],
    vectors: np.ndarray,
    *,
    min_topic_size: int,
    max_topics: int,
    params: BertopicParams | None = None,
) -> list[Topic]:
    """Cluster with BERTopic over an explicit UMAP + HDBSCAN pair.

    Both models are constructed here rather than left to BERTopic's defaults,
    for one reason: a default is a hyperparameter nobody wrote down. Passing
    them explicitly means every number that decides the result is in
    `BertopicParams`, is logged, and travels into `pipeline_runs.config_snapshot`
    with the run — which is the only thing that makes a months-old topic set
    arguable rather than merely present.

    Embeddings are the ones Stage 2 already computed, so BERTopic never
    downloads or runs a second model, and the clustering is over exactly the
    vectors the rest of the pipeline scores against.

    WHAT THIS COSTS. HDBSCAN assigns no document it cannot place densely,
    reporting them as cluster -1. Those are dropped, matching what
    `cluster_agglomerative` does with a sub-threshold document: an unassignable
    document is noise for this purpose and forcing it into the nearest topic
    would corrupt that topic. Expect a lower assigned share than average
    linkage gives, holding better-formed topics.

    Terms and labels are left to `label_topics`, not taken from BERTopic's own
    c-TF-IDF. One labelling path serves every clustering method, so a label
    means the same thing whichever method produced it, and Stage 3 gets the 30
    uni/bigram terms its lexicon matching needs rather than BERTopic's default
    10 unigrams.
    """
    try:
        from bertopic import BERTopic  # noqa: PLC0415
        from hdbscan import HDBSCAN  # noqa: PLC0415
        from umap import UMAP  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ImportError(
            "emergence.topics.method='bertopic' requires bertopic, umap-learn and "
            "hdbscan.\n  pip install -r requirements-ml.txt"
        ) from exc

    params = params or BertopicParams(min_cluster_size=min_topic_size)
    n = len(vectors)
    if n < max(params.n_neighbors, params.min_cluster_size) + 1:
        raise ValueError(
            f"BERTopic needs more than {max(params.n_neighbors, params.min_cluster_size)} "
            f"documents to fit UMAP and HDBSCAN; got {n}. Lower "
            "emergence.topics.bertopic.n_neighbors / min_topic_size, or use "
            "method: agglomerative for a corpus this small."
        )

    logger.info("BERTopic clustering %d documents — %s", n, params.describe())

    vectors = np.asarray(vectors, dtype=np.float64)
    model = BERTopic(
        umap_model=UMAP(
            # n_neighbors must stay below the corpus size or UMAP fails on a
            # small run — clamped rather than raised, since a sample run is a
            # legitimate thing to do and this is not the number it is testing.
            n_neighbors=max(2, min(params.n_neighbors, n - 1)),
            n_components=max(2, min(params.n_components, n - 2)),
            min_dist=params.min_dist,
            metric=params.metric,
            random_state=params.random_state,
        ),
        hdbscan_model=HDBSCAN(
            min_cluster_size=params.min_cluster_size,
            min_samples=params.min_samples,
            metric="euclidean",  # on UMAP output, not on the embeddings
            cluster_selection_method=params.cluster_selection_method,
            prediction_data=False,
        ),
        calculate_probabilities=False,
        verbose=False,
    )
    assignments, _ = model.fit_transform(list(texts), embeddings=vectors)

    by_cluster: dict[int, list[int]] = {}
    for index, raw in enumerate(assignments):
        cluster = int(raw)
        if cluster < 0:
            continue  # HDBSCAN outlier
        by_cluster.setdefault(cluster, []).append(index)

    topics: list[Topic] = []
    for members in by_cluster.values():
        if len(members) < min_topic_size:
            continue
        topics.append(
            Topic(
                topic_id="",
                member_indices=sorted(members),
                centroid=_normalise(vectors[members].mean(axis=0)),
            )
        )

    # Largest first, then a stable tiebreak so ids are reproducible.
    topics.sort(key=lambda t: (-t.size, t.member_indices[0]))
    if len(topics) > max_topics:
        logger.info(
            "Keeping the %d largest of %d topics (emergence.topics.max_topics)",
            max_topics, len(topics),
        )
        topics = topics[:max_topics]
    for rank, topic in enumerate(topics):
        topic.topic_id = f"T{rank:04d}"

    assigned = sum(t.size for t in topics)
    outliers = sum(1 for a in assignments if int(a) < 0)
    logger.info(
        "BERTopic formed %d topics (>= %d members) from %d documents; %d (%.0f%%) "
        "assigned, %d left as HDBSCAN outliers",
        len(topics), min_topic_size, n, assigned, 100.0 * assigned / max(n, 1), outliers,
    )
    return topics
