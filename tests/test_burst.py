"""Tests for Kleinberg burst detection (src/burst.py)."""

from __future__ import annotations

import pytest

from src.burst import burst_score, detect_bursts

TOTALS = [1000] * 8


def test_flat_series_never_bursts():
    result = detect_bursts([50] * 8, TOTALS)
    assert not result.is_bursting
    assert burst_score(result) == 0.0


def test_late_burst_is_detected_at_the_end():
    result = detect_bursts([20, 20, 22, 21, 25, 60, 120, 200], TOTALS)
    assert result.is_bursting
    assert max(result.burst_indices) == 7
    assert 0 not in result.burst_indices


def test_recency_is_rewarded_over_an_identical_earlier_burst():
    """The same burst shape scores higher when it is recent.

    A horizon scan that cannot distinguish a trend peaking now from one that
    peaked five years ago is not doing its job.
    """
    late = detect_bursts([20, 20, 22, 21, 25, 60, 120, 200], TOTALS)
    early = detect_bursts([200, 120, 60, 25, 21, 22, 20, 20], TOTALS)
    assert late.max_weight == pytest.approx(early.max_weight, rel=1e-6)
    assert burst_score(late) > burst_score(early)


def test_growth_matching_the_corpus_is_not_a_burst():
    """The control case. A topic growing only as fast as the whole corpus is
    not emerging — it is being carried. Detecting a burst here would make every
    topic in a growing corpus look like it was taking off."""
    counts = [10, 20, 40, 80, 160, 320, 640, 1280]
    totals = [c * 10 for c in counts]
    assert not detect_bursts(counts, totals).is_bursting


def test_degenerate_inputs_return_all_base_rather_than_raising():
    for counts, totals in (
        ([], []),
        ([10], [100]),
        ([0] * 8, TOTALS),
        ([1000] * 8, TOTALS),   # saturating: no burst state to enter
    ):
        result = detect_bursts(counts, totals)
        assert not result.is_bursting
        assert len(result.states) == len(counts)


def test_mismatched_series_lengths_raise():
    with pytest.raises(ValueError):
        detect_bursts([1, 2, 3], [10, 10])


def test_higher_gamma_makes_state_changes_costlier():
    """gamma is the transition cost; a large value should suppress short bursts."""
    counts = [20, 20, 20, 200, 20, 20, 20, 20]
    cheap = detect_bursts(counts, TOTALS, gamma=0.5)
    dear = detect_bursts(counts, TOTALS, gamma=50.0)
    assert cheap.is_bursting
    assert not dear.is_bursting


def test_burst_weights_are_zero_outside_burst_slices():
    result = detect_bursts([20, 20, 22, 21, 25, 60, 120, 200], TOTALS)
    for i, weight in enumerate(result.weights):
        if i not in result.burst_indices:
            assert weight == 0.0
        else:
            assert weight > 0.0
