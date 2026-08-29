"""Tests for Stage 3 and Stage 4 scoring."""

from __future__ import annotations

import numpy as np
import pytest

from src.embeddings import singularise
from src.stage3_scoring import lexicon_overlap, score_against_refs
from src.stage4_opportunity_index import (
    build_corpus_tokens,
    percentile_rank,
    policy_salience,
    redistribute_weights,
)


# --- lexical matching -----------------------------------------------------


def test_lexicon_matches_exact_phrases():
    assert lexicon_overlap(
        [("geographical indication", 0.9), ("provenance", 0.4)],
        ["geographical indication"],
    ) > 0.5


@pytest.mark.parametrize(
    "topic_term, lexicon_entry",
    [("trade marks", "trade mark"), ("patent", "patents"),
     ("critical technologies", "critical technology")],
)
def test_lexicon_matches_across_plural_forms(topic_term, lexicon_entry):
    """Nearly every IP term appears in both forms. Unmatched, 'trade mark' and
    'trade marks' score separately against the same objective."""
    assert lexicon_overlap([(topic_term, 1.0)], [lexicon_entry]) > 0.0


def test_lexicon_requires_all_tokens_of_a_phrase():
    """'quantum' alone must not match 'quantum computing' — partial phrase
    matching is the main source of false strategic-fit scores."""
    assert lexicon_overlap([("quantum", 0.9), ("biology", 0.5)], ["quantum computing"]) == 0.0


def test_lexicon_handles_empty_inputs():
    assert lexicon_overlap([], ["x"]) == 0.0
    assert lexicon_overlap([("x", 1.0)], []) == 0.0


def test_singularise_keeps_stems_ending_in_s():
    for word in ("analysis", "business", "status", "gas", "bias"):
        assert singularise(word) == word


# --- reference scoring ----------------------------------------------------


def test_topic_takes_its_best_reference_not_the_mean():
    """Averaging across unrelated objectives would bury a strong single match
    and make every topic converge on the same middling score."""
    refs = [
        {"code": "1.1", "label": "Unrelated", "lexicon": ["coral"], "weight": 1.0},
        {"code": "4.2", "label": "Digital", "lexicon": ["automation"], "weight": 1.0},
    ]
    ref_vectors = np.array([[1.0, 0.0], [0.0, 1.0]])
    score, label, _ = score_against_refs(
        np.array([0.0, 1.0]), [("automation", 1.0)], refs, ref_vectors,
        embedding_weight=0.7, lexicon_weight=0.3,
    )
    assert score > 0.9
    assert label.startswith("4.2")


def test_reference_weight_shifts_the_winner():
    """Objective weights express priority; a weighted reference should be able
    to win against an equally similar unweighted one."""
    refs = [
        {"code": "A", "label": "A", "lexicon": ["x"], "weight": 0.5},
        {"code": "B", "label": "B", "lexicon": ["x"], "weight": 1.3},
    ]
    ref_vectors = np.array([[1.0, 0.0], [1.0, 0.0]])
    _, label, _ = score_against_refs(
        np.array([1.0, 0.0]), [("x", 1.0)], refs, ref_vectors,
        embedding_weight=0.7, lexicon_weight=0.3,
    )
    assert label.startswith("B")


def test_no_references_scores_zero():
    score, label, sim = score_against_refs(
        np.array([1.0, 0.0]), [("x", 1.0)], [], np.zeros((0, 2)),
        embedding_weight=0.7, lexicon_weight=0.3,
    )
    assert (score, label, sim) == (0.0, "", 0.0)


# --- opportunity index ----------------------------------------------------


def test_percentile_rank_orders_and_bounds():
    assert percentile_rank([3, 1, 2]) == [1.0, 0.0, 0.5]
    assert percentile_rank([7]) == [0.5]
    assert percentile_rank([]) == []


def test_percentile_rank_averages_ties():
    """A population where most topics score zero must not hand them all 0.5."""
    ranks = percentile_rank([0, 0, 0, 0, 9])
    assert ranks[0] == ranks[1] == ranks[2] == ranks[3]
    assert ranks[4] == 1.0
    assert ranks[0] < 0.5


def test_weights_redistribute_over_available_components():
    """Disabling PatentsView must not silently shrink every index by 15%."""
    weights = {"a": 0.3, "b": 0.25, "c": 0.1, "d": 0.2, "e": 0.15}
    redistributed = redistribute_weights(weights, {"a", "b", "d"})
    assert sum(redistributed.values()) == pytest.approx(1.0)
    assert set(redistributed) == {"a", "b", "d"}
    assert redistributed["a"] > weights["a"]


def test_no_available_components_returns_empty_not_zero_weights():
    """An unmeasurable index and a zero index are different claims; the caller
    must be able to tell them apart and suppress rather than report zero."""
    assert redistribute_weights({"a": 1.0}, set()) == {}


def test_policy_salience_reflects_presence_in_the_strategy_corpus():
    tokens, size = build_corpus_tokens(
        "artificial intelligence " * 60 + "automated decision making " * 30
    )
    assert policy_salience([("artificial intelligence", 0.9)], tokens, size) > 0.8
    assert policy_salience([("quantum computing", 0.9)], tokens, size) == 0.0
    assert policy_salience([], tokens, size) == 0.0
