"""
src/burst.py — Kleinberg burst detection.

Implements the two-state case of the infinite-state automaton from:

    Kleinberg, J. (2003). "Bursty and Hierarchical Structure in Streams."
    Data Mining and Knowledge Discovery 7:373-397. DOI 10.1023/A:1024940629314

The premise, in Kleinberg's words: "the appearance of a topic in a document
stream is signaled by a 'burst of activity,' with certain features rising
sharply in frequency as the topic emerges."

WHAT THIS IMPLEMENTATION DOES

Given, per time slice, a count of documents on a topic (`r`) out of the total
documents in that slice (`d`), it finds the state sequence minimising

    total cost = sum_t -ln P(r_t | d_t, state_t)  +  sum_t gamma * ln(n) * [state changed]

with two states:
    state 0 (base)  : p0 = sum(r) / sum(d)   — the topic's overall share
    state 1 (burst) : p1 = min(p0 * s, 1)    — an elevated share

Optimised exactly by Viterbi over two states, not by a greedy scan. The output
is the state sequence plus a per-slice burst weight: the cost saved by being in
the burst state rather than the base state, which is Kleinberg's burst
*intensity* and is what should be compared across topics.

WHY IT IS BUILT RATHER THAN IMPORTED

The commonly cited Python implementation (nmarinsek/burst_detection) is
unmaintained and pulls in a heavier stack than the rest of this pipeline. The
two-state case is ~80 lines of numpy and is fully covered by tests here.

TWO CAVEATS THAT MATTER FOR READING RESULTS

1. Kleinberg runs over whatever stream you give it, including stop-words if you
   feed it raw terms. Stage 2 feeds it *topic* counts, which are already
   filtered, but if you repurpose this for raw keywords, pre-process first.

2. Short series produce spurious bursts. With eight annual slices there is very
   little evidence for any state sequence, and a single anomalous year can flip
   a topic to "bursting". `emergence.min_docs_per_topic` is the main guard;
   treat a burst on a thin topic as a prompt to look, not a finding.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

# Floor for probabilities, keeping logs finite when a slice has zero documents.
_EPS = 1e-12


@dataclass
class BurstResult:
    """Outcome of burst detection over one series."""

    states: list[int] = field(default_factory=list)          # 0 = base, 1 = burst
    weights: list[float] = field(default_factory=list)       # per-slice burst intensity
    p0: float = 0.0                                          # fitted base rate
    p1: float = 0.0                                          # fitted burst rate
    total_weight: float = 0.0                                # sum of weights while bursting
    max_weight: float = 0.0                                  # peak intensity

    @property
    def burst_indices(self) -> list[int]:
        return [i for i, s in enumerate(self.states) if s == 1]

    @property
    def is_bursting(self) -> bool:
        return any(self.states)


def detect_bursts(
    counts: Sequence[float],
    totals: Sequence[float],
    *,
    s: float = 2.0,
    gamma: float = 1.0,
) -> BurstResult:
    """Run two-state Kleinberg burst detection over aligned series.

    Parameters
    ----------
    counts : documents on this topic per slice (r_t)
    totals : total documents per slice (d_t)
    s      : burst-state rate multiplier; must be > 1
    gamma  : state-transition cost multiplier

    Returns a BurstResult. A degenerate input (no documents, one slice, or a
    topic present in every document) returns an all-base result rather than
    raising — a topic that never varies is genuinely not bursting.
    """
    r = np.asarray(counts, dtype=np.float64)
    d = np.asarray(totals, dtype=np.float64)
    if r.shape != d.shape:
        raise ValueError(f"counts and totals must align: {r.shape} vs {d.shape}")

    n = len(r)
    if n < 2 or d.sum() <= 0 or r.sum() <= 0:
        return BurstResult(states=[0] * n, weights=[0.0] * n)

    # Fitted rates. p1 is capped below 1 so its log-likelihood stays finite.
    p0 = float(r.sum() / d.sum())
    p1 = min(p0 * float(s), 1.0 - 1e-9)
    if p1 <= p0:
        # s <= 1, or the topic already saturates the stream: no burst state to
        # enter. Config validation rejects s <= 1, so this is the saturation case.
        return BurstResult(states=[0] * n, weights=[0.0] * n, p0=p0, p1=p1)

    cost = np.stack([_neg_log_likelihood(r, d, p0), _neg_log_likelihood(r, d, p1)])
    tau = float(gamma) * math.log(max(n, 2))  # transition cost, per Kleinberg

    states = _viterbi_two_state(cost, tau)

    # Burst intensity: how much better the burst state explains this slice.
    weights = np.where(np.array(states) == 1, np.maximum(cost[0] - cost[1], 0.0), 0.0)

    return BurstResult(
        states=states,
        weights=[float(w) for w in weights],
        p0=p0,
        p1=p1,
        total_weight=float(weights.sum()),
        max_weight=float(weights.max()) if len(weights) else 0.0,
    )


def _neg_log_likelihood(r: np.ndarray, d: np.ndarray, p: float) -> np.ndarray:
    """Per-slice -ln P(r | d, p) under a binomial, dropping the constant term.

    The binomial coefficient is identical for both states at each slice, so it
    cancels in every comparison and is omitted.
    """
    p = min(max(p, _EPS), 1.0 - _EPS)
    return -(r * math.log(p) + (d - r) * math.log(1.0 - p))


def _viterbi_two_state(cost: np.ndarray, tau: float) -> list[int]:
    """Exact minimum-cost state sequence over two states.

    cost[k, t] is the emission cost of state k at slice t; tau is the cost of
    changing state between slices. Kleinberg charges for entering the burst
    state; charging symmetrically (as here) is the standard simplification and
    keeps the recursion a plain two-row DP.
    """
    n = cost.shape[1]
    best = cost[:, 0].copy()          # best cost to reach each state at t
    back = np.zeros((2, n), dtype=int)  # argmin predecessor

    for t in range(1, n):
        new = np.empty(2)
        for k in (0, 1):
            stay = best[k]
            switch = best[1 - k] + tau
            if stay <= switch:
                new[k], back[k, t] = stay + cost[k, t], k
            else:
                new[k], back[k, t] = switch + cost[k, t], 1 - k
        best = new

    state = int(np.argmin(best))
    states = [0] * n
    for t in range(n - 1, -1, -1):
        states[t] = state
        state = int(back[state, t])
    return states


def burst_score(result: BurstResult, recency_bias: float = 0.5) -> float:
    """Collapse a BurstResult into one [0, 1] score for the emergence blend.

    Two things matter and are combined: how intense the strongest burst was,
    and how recent it was. A topic that burst five years ago and has been flat
    since is a different proposition from one bursting now, and a scan that
    cannot tell them apart is not doing horizon scanning.

    `recency_bias` sets the split: 0.0 ignores timing entirely, 1.0 scores only
    on timing.
    """
    if not result.is_bursting or result.max_weight <= 0:
        return 0.0

    # Squash unbounded intensity into [0, 1). The scale (10.0) is arbitrary but
    # consistent, so scores stay comparable across topics within a run.
    intensity = result.max_weight / (result.max_weight + 10.0)

    n = len(result.states)
    last_burst = max(result.burst_indices)
    recency = (last_burst + 1) / n if n else 0.0

    b = min(max(recency_bias, 0.0), 1.0)
    return float((1.0 - b) * intensity + b * intensity * recency)
