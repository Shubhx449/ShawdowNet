"""
Statistics helper for aggregating a metric's values across independent runs.

Uses the normal approximation (z=1.96) for the 95% confidence interval
rather than a t-distribution critical value. This is a documented
approximation, not a hidden shortcut: for small run counts (the paper's own
default is 10 runs) a t-distribution CI would be slightly wider and more
correct. If stricter statistical rigor is needed later, swap this function's
internals for scipy.stats.t -- nothing else in this module depends on the
approximation specifically.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class AggregatedMetric:
    mean: Optional[float]
    std: Optional[float]
    ci_95: Optional[Tuple[float, float]]
    n: int

    def to_dict(self) -> dict:
        return {
            "mean": round(self.mean, 4) if self.mean is not None else None,
            "std": round(self.std, 4) if self.std is not None else None,
            "ci_95": [round(v, 4) for v in self.ci_95] if self.ci_95 is not None else None,
            "n": self.n,
        }


def mean_std_ci(values: List[float], confidence_z: float = 1.96) -> AggregatedMetric:
    """
    values: the metric's value from each independent run (None values should
    already be filtered out by the caller -- a metric that was None in some
    runs, e.g. avg_end_to_end_delay_s when nothing was delivered, shouldn't
    silently count as 0 and skew the mean).
    """
    n = len(values)
    if n == 0:
        return AggregatedMetric(mean=None, std=None, ci_95=None, n=0)
    mean_ = statistics.mean(values)
    if n < 2:
        return AggregatedMetric(mean=mean_, std=0.0, ci_95=None, n=n)
    std_ = statistics.stdev(values)
    margin = confidence_z * std_ / math.sqrt(n)
    return AggregatedMetric(mean=mean_, std=std_, ci_95=(mean_ - margin, mean_ + margin), n=n)
