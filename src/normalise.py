"""
src/normalise.py — shared score normalisation.

Small module so that Stage 2 and Stage 4 can share `percentile_rank` without
one stage importing the other. The stages are meant to be independent; a
statistical helper is not a reason to couple them.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


def percentile_rank(values: Sequence[float]) -> list[float]:
    """Rank values into [0, 1] by their position in the population.

    Ties share the mean of the positions they span, so a population where most
    entries are zero does not hand all of them a spurious 0.5.

    Used wherever measures on different natural scales have to be combined. A
    weighted sum of raw values is dominated by whichever measure happens to
    have the widest range, which makes the configured weights describe
    something other than what the code does.
    """
    array = np.asarray(values, dtype=np.float64)
    n = len(array)
    if n == 0:
        return []
    if n == 1:
        return [0.5]

    order = np.argsort(array, kind="stable")
    ranks = np.empty(n, dtype=np.float64)
    sorted_values = array[order]

    start = 0
    for i in range(1, n + 1):
        if i == n or sorted_values[i] != sorted_values[start]:
            ranks[order[start:i]] = (start + i - 1) / 2.0
            start = i
    return [float(r / (n - 1)) for r in ranks]
