"""Tests for Stage 2 emergence maths (src/stage2_emergence.py)."""

from __future__ import annotations

from collections import Counter

import numpy as np
import pytest

from src.config import load_config
from src.stage2_emergence import (
    _normalised_entropy,
    _squash_cagr,
    assign_horizon,
    citation_percentiles,
    classify_signal,
    compute_cagr,
    compute_uncertainty,
    fit_logistic_maturity,
    topic_series,
)


@pytest.fixture(scope="module")
def config():
    return load_config()


# --- growth ---------------------------------------------------------------


def test_cagr_is_zero_for_a_flat_series():
    assert compute_cagr([50] * 8) == pytest.approx(0.0, abs=1e-9)


def test_cagr_is_positive_for_growth_and_negative_for_decline():
    assert compute_cagr([1, 2, 4, 8, 16, 32, 64, 128]) > 0.5
    assert compute_cagr([100, 80, 60, 40, 20, 10, 5, 2]) < 0.0


def test_cagr_requires_a_minimum_series_length():
    """Two points cannot support a growth rate; returning 0 beats inventing one."""
    assert compute_cagr([1, 100], min_slices=4) == 0.0


def test_squash_cagr_is_monotonic_and_bounded():
    assert _squash_cagr(-1.0) == 0.0
    assert _squash_cagr(1.0) == pytest.approx(0.5)
    assert _squash_cagr(100.0) < 1.0
    assert _squash_cagr(5.0) > _squash_cagr(2.0)


# --- maturity and horizons ------------------------------------------------


def test_early_exponential_growth_is_horizon_3(config):
    """The regression that matters most.

    An early-exponential series must read as immature (H3). A logit-linearised
    fit reports it as saturated (H1) — the exact opposite — because the logit
    of y/K is near-linear for any large K. Getting this backwards would put
    every genuinely new technology in the 'sustain the current system' band.
    """
    maturity, quality = fit_logistic_maturity([0, 0, 1, 1, 2, 4, 8, 20])
    assert maturity < 0.2
    assert quality > 0.8
    assert assign_horizon(maturity, config) == "H3"


def test_saturated_series_is_horizon_1(config):
    maturity, _ = fit_logistic_maturity([100, 100, 100, 100, 100, 20, 5, 1])
    assert maturity > 0.9
    assert assign_horizon(maturity, config) == "H1"


def test_mid_curve_series_is_horizon_2(config):
    maturity, _ = fit_logistic_maturity([1, 2, 5, 15, 40, 80, 120, 140])
    assert assign_horizon(maturity, config) == "H2"


def test_short_series_returns_neutral_maturity_with_zero_confidence():
    maturity, quality = fit_logistic_maturity([1, 2])
    assert maturity == 0.5
    assert quality == 0.0


def test_leading_zeros_do_not_distort_the_fit():
    """A topic that starts late should be fitted from where it starts."""
    late = fit_logistic_maturity([0, 0, 0, 0, 1, 3, 9, 27])[0]
    same_without_padding = fit_logistic_maturity([1, 3, 9, 27, 81, 243])[0]
    assert late < 0.35
    assert same_without_padding < 0.35


# --- attributes -----------------------------------------------------------


def test_normalised_entropy_bounds():
    assert _normalised_entropy(Counter({"a": 10})) == 0.0             # one actor
    assert _normalised_entropy(Counter(dict.fromkeys("abcd", 5))) == pytest.approx(1.0)
    assert _normalised_entropy(Counter({"a": 97, "b": 1, "c": 1, "d": 1})) < 0.2


def test_uncertainty_rises_with_actor_dispersion():
    concentrated = [
        {"source": "openalex", "institutions": ["Lab A"]} for _ in range(10)
    ]
    dispersed = [
        {"source": s, "institutions": [f"Lab {i}"]}
        for i, s in enumerate(["openalex", "arxiv", "crossref", "gdelt"] * 3)
    ]
    assert compute_uncertainty(dispersed) > compute_uncertainty(concentrated)


def test_citation_percentiles_are_computed_within_each_source():
    """arXiv reports no citations. Ranked globally, every preprint would sit at
    the bottom and the fastest-moving evidence in the corpus would be
    systematically penalised."""
    documents = (
        [{"source": "arxiv", "citation_count": 0} for _ in range(5)]
        + [{"source": "crossref", "citation_count": c} for c in (0, 10, 100)]
    )
    percentiles = citation_percentiles(documents)
    assert percentiles["crossref"][0] < percentiles["crossref"][100]


def test_a_source_with_no_citation_variation_scores_neutral_impact():
    """A percentile over a constant is 1.0 for every document.

    arXiv and GDELT report no citations at all, so a naive within-source
    percentile hands maximum impact to every preprint and every news headline
    in the corpus — which is what happened on the first real run. No signal
    must read as neutral, not as maximum.
    """
    documents = (
        [{"source": "arxiv", "citation_count": 0} for _ in range(5)]
        + [{"source": "gdelt", "citation_count": 0} for _ in range(50)]
        + [{"source": "crossref", "citation_count": c} for c in (0, 10, 100)]
    )
    percentiles = citation_percentiles(documents)
    assert percentiles["arxiv"][0] == 0.5
    assert percentiles["gdelt"][0] == 0.5
    # A source that does report citations keeps its full spread.
    assert percentiles["crossref"][0] != 0.5


# --- classification and series -------------------------------------------


def test_signal_quadrants():
    assert classify_signal(0.01, 0.9, 0.05, 0.5) == "weak"
    assert classify_signal(0.20, 0.9, 0.05, 0.5) == "strong"
    assert classify_signal(0.20, 0.1, 0.05, 0.5) == "latent"
    assert classify_signal(0.01, 0.1, 0.05, 0.5) == "noise"


def test_topic_series_zero_fills_the_full_corpus_span():
    """Growth must be measured from the corpus start, not the topic's own."""
    docs = [{"time_slice": "2024"}, {"time_slice": "2024"}, {"time_slice": "2025"}]
    assert topic_series(docs, ["2022", "2023", "2024", "2025"]) == [0, 0, 2, 1]


# --- attribute normalisation ---------------------------------------------


def test_attributes_are_rank_normalised_so_weights_mean_what_they_say():
    """The five Rotolo attributes have very different natural ranges.

    Under the hashing backend, novelty spans roughly 0.72-0.88 while growth
    spans 0.0-0.95. A weighted sum of raw values is therefore driven almost
    entirely by the wide-ranging attributes regardless of the configured
    weights — measured on a realistic population, novelty configured at 0.25
    drove about 6% of the ranking spread and growth at 0.30 drove about 49%.

    Rank-normalising first gives every attribute the same spread, so a weight
    of 0.25 buys 25% of the influence.
    """
    from src.normalise import percentile_rank

    narrow = [0.72, 0.75, 0.80, 0.85, 0.88]
    wide = [0.00, 0.20, 0.50, 0.80, 0.95]

    ranked_narrow = percentile_rank(narrow)
    ranked_wide = percentile_rank(wide)

    assert np.std(narrow) < np.std(wide) / 3          # the problem
    assert np.std(ranked_narrow) == pytest.approx(np.std(ranked_wide))  # the fix
    # Order is preserved — normalisation changes scale, not ranking.
    assert ranked_narrow == sorted(ranked_narrow)


def test_novelty_is_distance_from_the_early_corpus():
    from src.stage2_emergence import compute_novelty

    early = np.array([1.0, 0.0])
    assert compute_novelty(np.array([1.0, 0.0]), early) == pytest.approx(0.0)  # identical
    assert compute_novelty(np.array([0.0, 1.0]), early) == pytest.approx(1.0)  # orthogonal
    # A missing centroid returns neutral rather than a misleading extreme.
    assert compute_novelty(None, early) == 0.5
    assert compute_novelty(np.zeros(2), early) == 0.5
