"""RoutingMatrix — empirical success probability matrix.

Blueprint §10:
    | Worker   | Coding | Research | Planning | Audio | Tool Use |
    | -------- | -----: | -------: | -------: | ----: | -------: |
    | Qwen 3B  |   0.78 |     0.55 |     0.52 |  0.20 |     0.65 |
    | Qwen 8B  |   0.88 |     0.70 |     0.70 |  0.30 |     0.80 |
    | DeepSeek |   0.92 |     0.88 |     0.84 |  0.35 |     0.90 |

    These numbers must never be manually treated as truth.
    They should emerge from verified task outcomes.

The RoutingMatrix stores and queries empirical success probabilities.
It supports:
    - initial seeding (manual priors)
    - observation recording (verified outcomes)
    - Bayesian-style updates (prior → posterior)
    - query by worker + task type
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RoutingObservation:
    """One verified routing outcome — the training data for the matrix."""

    worker_id: str
    task_type: str
    success: bool
    verification_score: float = 0.0
    latency_ms: float = 0.0
    cost: float = 0.0
    context_tokens: int = 0
    attempt: int = 1
    msl_version: str = "v1"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


class RoutingMatrix:
    """Empirical success probability matrix.

    Stores probabilities as (success_count, total_count) per (worker, task_type).
    Queries return the empirical success rate with Laplace smoothing.

    Usage::

        matrix = RoutingMatrix()
        matrix.seed("qwen3b", "implementation", 0.78)  # prior
        matrix.record(RoutingObservation(
            worker_id="qwen3b", task_type="implementation", success=True,
        ))
        prob = matrix.get_probability("qwen3b", "implementation")
    """

    def __init__(self, *, smoothing_alpha: float = 1.0) -> None:
        # (worker_id, task_type) → (success_count, total_count)
        self._counts: Dict[Tuple[str, str], Tuple[int, int]] = {}
        self._smoothing_alpha = smoothing_alpha
        self._observations: List[RoutingObservation] = []

    def seed(self, worker_id: str, task_type: str, probability: float, *, total: int = 10) -> None:
        """Seed a prior probability.  ``total`` is the virtual observation count."""
        successes = int(probability * total)
        self._counts[(worker_id, task_type)] = (successes, total)

    def record(self, observation: RoutingObservation) -> None:
        """Record a verified routing outcome."""
        self._observations.append(observation)
        key = (observation.worker_id, observation.task_type)
        successes, total = self._counts.get(key, (0, 0))
        if observation.success:
            successes += 1
        total += 1
        self._counts[key] = (successes, total)
        logger.debug(
            "recorded observation: %s/%s success=%s → %.3f",
            observation.worker_id,
            observation.task_type,
            observation.success,
            self.get_probability(observation.worker_id, observation.task_type),
        )

    def get_probability(self, worker_id: str, task_type: str) -> float:
        """Get the empirical success probability with Laplace smoothing.

        Returns a value in [0.0, 1.0].
        """
        successes, total = self._counts.get((worker_id, task_type), (0, 0))
        # Laplace smoothing: avoids 0.0 and 1.0 extremes.
        return (successes + self._smoothing_alpha) / (total + 2 * self._smoothing_alpha)

    def get_all_probabilities(self) -> Dict[str, Dict[str, float]]:
        """Get all probabilities organized by worker → task_type."""
        result: Dict[str, Dict[str, float]] = {}
        for (worker_id, task_type) in self._counts:
            if worker_id not in result:
                result[worker_id] = {}
            result[worker_id][task_type] = self.get_probability(worker_id, task_type)
        return result

    def get_worker_stats(self, worker_id: str) -> Dict[str, Any]:
        """Get aggregate statistics for a worker across all task types."""
        total_success = 0
        total_attempts = 0
        task_types: List[str] = []

        for (wid, tt), (s, t) in self._counts.items():
            if wid == worker_id:
                total_success += s
                total_attempts += t
                task_types.append(tt)

        return {
            "worker_id": worker_id,
            "task_types": task_types,
            "total_successes": total_success,
            "total_attempts": total_attempts,
            "overall_probability": (
                (total_success + self._smoothing_alpha)
                / (total_attempts + 2 * self._smoothing_alpha)
                if total_attempts > 0
                else 0.5
            ),
        }

    def get_task_type_stats(self, task_type: str) -> Dict[str, Any]:
        """Get aggregate statistics for a task type across all workers."""
        workers: List[str] = []
        total_success = 0
        total_attempts = 0

        for (wid, tt), (s, t) in self._counts.items():
            if tt == task_type:
                workers.append(wid)
                total_success += s
                total_attempts += t

        return {
            "task_type": task_type,
            "workers": workers,
            "total_successes": total_success,
            "total_attempts": total_attempts,
        }

    def save(self, path: str) -> None:
        """Persist the matrix to a JSON file."""
        data = {
            "counts": {f"{w}|{t}": [s, n] for (w, t), (s, n) in self._counts.items()},
            "observations": [
                {
                    "worker_id": o.worker_id,
                    "task_type": o.task_type,
                    "success": o.success,
                    "verification_score": o.verification_score,
                    "latency_ms": o.latency_ms,
                    "cost": o.cost,
                    "timestamp": o.timestamp,
                }
                for o in self._observations
            ],
            "smoothing_alpha": self._smoothing_alpha,
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self, path: str) -> None:
        """Load the matrix from a JSON file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self._smoothing_alpha = data.get("smoothing_alpha", 1.0)
        self._counts = {}
        raw_counts: Dict[str, list] = data.get("counts", {})
        for key, val in raw_counts.items():
            parts = key.split("|", 1)
            if len(parts) == 2 and isinstance(val, list) and len(val) == 2:
                self._counts[(parts[0], parts[1])] = (int(val[0]), int(val[1]))
        self._observations = [
            RoutingObservation(**obs) for obs in data.get("observations", [])
        ]
