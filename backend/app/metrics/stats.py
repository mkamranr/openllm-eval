"""Descriptive statistics over individual observations (spec §5.5, §11.3).

Percentiles use linear interpolation between closest ranks (the "linear" / Hyndman-Fan type 7
method, the default in NumPy and spreadsheet PERCENTILE.INC). They are always computed from the
individual request observations, never by averaging per-batch percentiles.
Every function returns ``None`` for an empty sample, never 0.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

PERCENTILE_METHOD = "linear interpolation between closest ranks (type 7)"


def percentile(values: Sequence[float], q: float) -> float | None:
    if not 0 <= q <= 100:
        raise ValueError("q must be between 0 and 100")
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * q / 100
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(ordered[low])
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def mean(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def stdev(values: Sequence[float]) -> float | None:
    """Sample standard deviation (n - 1). ``None`` for fewer than two observations."""
    if len(values) < 2:
        return None
    m = math.fsum(values) / len(values)
    return math.sqrt(math.fsum((v - m) ** 2 for v in values) / (len(values) - 1))


def distribution(values: Sequence[float]) -> dict[str, float | None]:
    """Summary used for latency-like metrics."""
    return {
        "mean": mean(values),
        "stddev": stdev(values),
        "min": float(min(values)) if values else None,
        "p50": percentile(values, 50),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": float(max(values)) if values else None,
    }
