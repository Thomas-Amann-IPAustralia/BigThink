"""Tests for embeddings and topic formation."""

from __future__ import annotations

import numpy as np
import pytest

from src.embeddings import HashingEmbedder, centroid, cosine_similarity, normalise_tokens
from src.topics import cluster_agglomerative, label_topics

CORPUS = (
    ["Quantum error correction with surface codes for fault tolerant qubits"] * 6
    + ["Fault tolerant quantum computing using topological qubit error correction"] * 6
    + ["Trade mark examination practice and opposition procedures at the IP office"] * 6
    + ["Trade mark opposition and registration examination guidelines for examiners"] * 6
    + ["Large language models for automated patent claim classification and search"] * 6
    + ["Unrelated study of coral reef bleaching in tropical marine ecosystems"] * 2
)


@pytest.fixture(scope="module")
def vectors():
    return HashingEmbedder(2048).fit(CORPUS).encode(CORPUS)


def test_hashing_embedder_is_deterministic_across_instances():
    """Week-over-week comparison is the point of this pipeline; vectors must not
    depend on Python's randomised hash seed."""
    a = HashingEmbedder(2048).fit(CORPUS).encode(CORPUS)
    b = HashingEmbedder(2048).fit(CORPUS).encode(CORPUS)
    assert np.allclose(a, b)


def test_related_documents_are_more_similar_than_unrelated(vectors):
    similarity = cosine_similarity(vectors, vectors)
    assert similarity[0, 1] > similarity[0, 12]      # quantum pair vs quantum/trade-mark
    assert similarity[12, 18] > similarity[12, 0]    # trade-mark pair


def test_empty_text_produces_a_zero_vector_not_a_nan():
    vector = HashingEmbedder(512).fit(["x y z"]).encode([""])[0]
    assert np.linalg.norm(vector) == 0.0
    assert not np.isnan(vector).any()


def test_centroid_of_nothing_is_zero_length_safe():
    assert centroid(np.zeros((0, 4))).shape == (4,)


def test_stopwords_and_boilerplate_are_dropped():
    tokens = normalise_tokens("The results of this study show a NOVEL quantum approach")
    assert tokens == ["quantum"]


def test_clustering_merges_paraphrases_and_excludes_noise(vectors):
    topics = cluster_agglomerative(
        vectors, threshold=0.18, min_topic_size=4, max_topics=50
    )
    label_topics(topics, CORPUS)

    assert len(topics) == 3
    # The two quantum phrasings and the two trade-mark phrasings each merge.
    assert sorted(t.size for t in topics) == [6, 12, 12]
    # The two coral documents match nothing and are left out rather than forced
    # into the nearest topic, which would corrupt that topic's centroid.
    assert sum(t.size for t in topics) == len(CORPUS) - 2


def test_topic_ids_are_stable_for_the_same_input(vectors):
    first = cluster_agglomerative(vectors, threshold=0.18, min_topic_size=4, max_topics=50)
    second = cluster_agglomerative(vectors, threshold=0.18, min_topic_size=4, max_topics=50)
    assert [t.topic_id for t in first] == [t.topic_id for t in second]
    assert [t.member_indices for t in first] == [t.member_indices for t in second]


def test_labels_drop_terms_subsumed_by_higher_ranked_ones(vectors):
    """Without this, labels read 'patent / patent examination / examination' —
    technically the top terms, useless to a reader."""
    topics = cluster_agglomerative(vectors, threshold=0.18, min_topic_size=4, max_topics=50)
    label_topics(topics, CORPUS)
    for topic in topics:
        parts = topic.label.split(" / ")
        for i, part in enumerate(parts):
            for other in parts[i + 1:]:
                assert part not in other and other not in part


def test_max_topics_is_respected():
    corpus = [f"unique subject number {i} with distinct vocabulary {i}" for i in range(40)]
    vecs = HashingEmbedder(2048).fit(corpus).encode(corpus)
    topics = cluster_agglomerative(vecs, threshold=0.9, min_topic_size=1, max_topics=5)
    assert len(topics) <= 5


# --- numeric noise --------------------------------------------------------


def test_numeric_tokens_are_dropped():
    """News headlines are full of bare numbers, which otherwise become topic
    labels like 'index / 17 / 750 000'. A number is never what a topic is
    about."""
    tokens = normalise_tokens("Fine of 750 000 euros over 000km fibre optic network in 2026")
    assert not any(any(c.isdigit() for c in t) for t in tokens)
    assert "fibre" in tokens and "optic" in tokens


def test_attaching_documents_does_not_move_centroids():
    """Held-out documents are attached after centroids are fixed.

    If attachment ran before, a few thousand news headlines would drag every
    research topic toward the news cycle — which is exactly the failure that
    made topic-forming sources a separate config setting.
    """
    from src.stage2_emergence import _attach_documents
    from src.topics import Topic

    corpus = ["quantum error correction qubit"] * 6 + ["trade mark examination opposition"] * 6
    held_out = ["quantum computing breakthrough announced"]
    embedder = HashingEmbedder(2048).fit(corpus + held_out)
    all_vectors = embedder.encode(corpus + held_out)

    topics = cluster_agglomerative(
        all_vectors[: len(corpus)], threshold=0.18, min_topic_size=4, max_topics=10
    )
    centroids_before = [t.centroid.copy() for t in topics]
    sizes_before = [t.size for t in topics]

    _attach_documents(topics, all_vectors, [len(corpus)], threshold=0.05)

    assert all(
        np.array_equal(before, topic.centroid)
        for before, topic in zip(centroids_before, topics)
    )
    assert sum(t.size for t in topics) == sum(sizes_before) + 1


def test_unattachable_documents_are_left_out():
    from src.stage2_emergence import _attach_documents

    corpus = ["quantum error correction qubit"] * 6
    held_out = ["coral reef bleaching tropical marine"]
    embedder = HashingEmbedder(2048).fit(corpus + held_out)
    vectors = embedder.encode(corpus + held_out)
    topics = cluster_agglomerative(
        vectors[: len(corpus)], threshold=0.18, min_topic_size=4, max_topics=10
    )
    before = sum(t.size for t in topics)
    _attach_documents(topics, vectors, [len(corpus)], threshold=0.18)
    assert sum(t.size for t in topics) == before


# --- average-linkage clustering -------------------------------------------
#
# The 2026-08-30 run's largest topic held 57% of everything assigned, under an
# incoherent label, with its centroid at cosine 0.955 from the corpus centroid.
# That is what `cluster_leader` does by construction. These pin the properties
# that make `cluster_agglomerative` unable to repeat it.


def _blobs(rng, spec, dim=64, spread=0.06):
    """Well-separated clusters of L2-normalised vectors."""
    import numpy as np

    parts = []
    for size in spec:
        centre = rng.normal(0, 1, dim)
        centre /= np.linalg.norm(centre)
        v = centre + rng.normal(0, spread, (size, dim))
        parts.append(v / np.linalg.norm(v, axis=1, keepdims=True))
    return np.vstack(parts)


def test_average_linkage_recovers_separated_groups():
    import numpy as np

    from src.topics import cluster_agglomerative

    rng = np.random.default_rng(0)
    topics = cluster_agglomerative(
        _blobs(rng, [40, 30, 25]), threshold=0.5, min_topic_size=8, max_topics=20
    )
    assert sorted(t.size for t in topics) == [25, 30, 40]


def test_average_linkage_is_order_invariant():
    """The chronological ordering of the corpus must not decide which topics
    form. `cluster_leader` seeded clusters from the oldest documents, so a
    topic first appearing in 2024 competed against centroids that had already
    absorbed eight years of text."""
    import numpy as np

    from src.topics import cluster_agglomerative

    rng = np.random.default_rng(3)
    vectors = _blobs(rng, [30, 25, 20, 15])
    order = rng.permutation(len(vectors))

    first = cluster_agglomerative(
        vectors, threshold=0.5, min_topic_size=8, max_topics=20
    )
    shuffled = cluster_agglomerative(
        vectors[order], threshold=0.5, min_topic_size=8, max_topics=20
    )

    as_sets = {frozenset(t.member_indices) for t in first}
    remapped = {
        frozenset(int(order[i]) for i in t.member_indices) for t in shuffled
    }
    assert as_sets == remapped


def test_documents_below_the_threshold_are_left_unassigned():
    """Forcing an unassignable document into its nearest topic corrupts that
    topic's centroid, which is how a catch-all starts."""
    import numpy as np

    from src.topics import cluster_agglomerative

    rng = np.random.default_rng(5)
    vectors = np.vstack([_blobs(rng, [30]), _blobs(rng, [1] * 20, spread=0.9)])
    topics = cluster_agglomerative(
        vectors, threshold=0.5, min_topic_size=8, max_topics=20
    )
    assert sum(t.size for t in topics) < len(vectors)


def test_a_cluster_gets_harder_to_join_as_it_grows():
    """The property `cluster_leader` lacked. Linkage is the mean pairwise
    similarity to a cluster's members, so absorbing unrelated material lowers
    a cluster's affinity for everything else instead of raising it."""
    import numpy as np

    from src.topics import cluster_agglomerative

    rng = np.random.default_rng(11)
    vectors = _blobs(rng, [200, 20], spread=0.06)
    topics = cluster_agglomerative(
        vectors, threshold=0.5, min_topic_size=8, max_topics=20
    )
    assert len(topics) == 2, "the large group must not swallow the small one"


def test_the_pairwise_guard_refuses_rather_than_exhausting_memory():
    import numpy as np
    import pytest

    from src.topics import cluster_agglomerative

    with pytest.raises(ValueError, match="max_pairwise"):
        cluster_agglomerative(
            np.zeros((50, 4)), threshold=0.5, min_topic_size=2,
            max_topics=10, max_pairwise=10,
        )


def test_empty_documents_do_not_drag_a_centroid_toward_zero():
    import numpy as np

    from src.topics import cluster_agglomerative

    rng = np.random.default_rng(7)
    vectors = np.vstack([_blobs(rng, [20]), np.zeros((5, 64))])
    topics = cluster_agglomerative(
        vectors, threshold=0.5, min_topic_size=8, max_topics=20
    )
    assigned = {i for t in topics for i in t.member_indices}
    assert assigned.isdisjoint(range(20, 25))


def test_max_topics_keeps_the_largest_rather_than_dropping_silently():
    """`cluster_leader` stopped assigning once it hit the cap, so under
    chronological ordering the documents it dropped were the recent ones."""
    import numpy as np

    from src.topics import cluster_agglomerative

    rng = np.random.default_rng(13)
    topics = cluster_agglomerative(
        _blobs(rng, [40, 30, 20, 10]), threshold=0.5, min_topic_size=8, max_topics=2
    )
    assert [t.size for t in topics] == [40, 30]


# --- BERTopic (PROJECT_STATE issue 2) -------------------------------------
# The default clustering method since 2026-08-31. These need requirements-ml.txt
# and skip without it — but they make no network call: BERTopic is handed the
# embeddings Stage 2 already computed, so it never loads a model of its own.


def _bertopic_blobs(n_per=40, dims=32, spread=0.05, seed=0):
    """Three well-separated clusters of unit vectors, plus matching text."""
    rng = np.random.default_rng(seed)
    centres = rng.normal(size=(3, dims))
    centres /= np.linalg.norm(centres, axis=1, keepdims=True)
    vocab = [
        ["quantum", "qubit", "error", "correction"],
        ["trade", "mark", "examination", "opposition"],
        ["battery", "storage", "renewable", "grid"],
    ]
    vectors, texts = [], []
    for c in range(3):
        for i in range(n_per):
            v = centres[c] + rng.normal(scale=spread, size=dims)
            vectors.append(v / np.linalg.norm(v))
            texts.append(" ".join(vocab[c]) + f" study number {i}")
    return np.array(vectors), texts


def _bertopic():
    pytest.importorskip("bertopic")
    pytest.importorskip("hdbscan")
    pytest.importorskip("umap")
    from src.topics import BertopicParams, cluster_bertopic

    return cluster_bertopic, BertopicParams


def test_bertopic_recovers_well_separated_clusters():
    cluster_bertopic, BertopicParams = _bertopic()
    vectors, texts = _bertopic_blobs()

    topics = cluster_bertopic(
        texts, vectors, min_topic_size=8, max_topics=50,
        params=BertopicParams(random_state=42, min_cluster_size=8),
    )

    assert len(topics) == 3
    assert sorted(t.size for t in topics) == [40, 40, 40]


def test_bertopic_is_deterministic_under_a_fixed_seed():
    """The property that makes a BERTopic run reviewable at all.

    UMAP's initialisation is stochastic. Unseeded, two runs over an identical
    corpus disagree about what the topics are, and a topic set nobody can
    reproduce is one nobody can check. This is the whole reason
    `emergence.topics.bertopic.random_state` exists and is validated.
    """
    cluster_bertopic, BertopicParams = _bertopic()
    vectors, texts = _bertopic_blobs()
    params = BertopicParams(random_state=42, min_cluster_size=8)

    first = cluster_bertopic(texts, vectors, min_topic_size=8, max_topics=50, params=params)
    second = cluster_bertopic(texts, vectors, min_topic_size=8, max_topics=50, params=params)

    assert [t.member_indices for t in first] == [t.member_indices for t in second]


def test_bertopic_honours_max_topics():
    cluster_bertopic, BertopicParams = _bertopic()
    vectors, texts = _bertopic_blobs()

    topics = cluster_bertopic(
        texts, vectors, min_topic_size=8, max_topics=2,
        params=BertopicParams(random_state=42, min_cluster_size=8),
    )

    assert len(topics) == 2, "the cap must bind, keeping the largest"
    assert topics[0].size >= topics[1].size


def test_bertopic_drops_clusters_below_min_topic_size():
    """A composite computed on 8 documents looks identical to one on 800."""
    cluster_bertopic, BertopicParams = _bertopic()
    vectors, texts = _bertopic_blobs()

    topics = cluster_bertopic(
        texts, vectors, min_topic_size=100, max_topics=50,
        params=BertopicParams(random_state=42, min_cluster_size=8),
    )

    assert topics == [], "40-document clusters cannot survive a floor of 100"


def test_bertopic_topic_ids_are_contiguous_and_ordered_by_size():
    cluster_bertopic, BertopicParams = _bertopic()
    vectors, texts = _bertopic_blobs()

    topics = cluster_bertopic(
        texts, vectors, min_topic_size=8, max_topics=50,
        params=BertopicParams(random_state=42, min_cluster_size=8),
    )

    assert [t.topic_id for t in topics] == [f"T{i:04d}" for i in range(len(topics))]
    assert [t.size for t in topics] == sorted((t.size for t in topics), reverse=True)


def test_bertopic_centroids_are_normalised():
    """Stage 2 and Stage 3 take cosines against these directly."""
    cluster_bertopic, BertopicParams = _bertopic()
    vectors, texts = _bertopic_blobs()

    topics = cluster_bertopic(
        texts, vectors, min_topic_size=8, max_topics=50,
        params=BertopicParams(random_state=42, min_cluster_size=8),
    )

    for topic in topics:
        assert np.isclose(np.linalg.norm(topic.centroid), 1.0)


def test_bertopic_refuses_a_corpus_too_small_to_fit():
    """A clear message rather than a UMAP traceback three hours into a scan."""
    cluster_bertopic, BertopicParams = _bertopic()
    vectors, texts = _bertopic_blobs(n_per=2)

    with pytest.raises(ValueError, match="more than"):
        cluster_bertopic(
            texts, vectors, min_topic_size=8, max_topics=50,
            params=BertopicParams(random_state=42, n_neighbors=15, min_cluster_size=8),
        )


def test_bertopic_params_carry_every_hyperparameter_into_the_log():
    """`describe()` is what puts the seed in the run log; keep it complete."""
    _, BertopicParams = _bertopic()
    described = BertopicParams(random_state=7, min_cluster_size=9).describe()

    for expected in ("seed=7", "n_neighbors=", "n_components=", "min_dist=",
                     "metric=", "min_cluster_size=9", "min_samples=",
                     "cluster_selection_method="):
        assert expected in described, f"{expected} missing from {described!r}"
