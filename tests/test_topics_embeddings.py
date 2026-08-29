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
