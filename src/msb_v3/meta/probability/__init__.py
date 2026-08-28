"""META-1C: Probability Engine — empirical routing intelligence.

Blueprint §10, §11:
    The probability matrix represents: estimated probability that this worker
    will successfully complete this task under the current task specification.

    The matrix learns from real results.  Every observation records:
        worker, task class, MSL version, compiler version, context version,
        skill, tools, attempt, verification result, latency, cost, failure.

    Bayesian updates: prior → execution → verification → posterior.

    The system may learn.  It may not silently rewrite its own governance.
"""

from msb_v3.meta.probability.historical_performance import (
    HistoricalPerformance,
    WorkerStats,
)
from msb_v3.meta.probability.routing_matrix import RoutingMatrix

__all__ = [
    "HistoricalPerformance",
    "RoutingMatrix",
    "WorkerStats",
]
