"""HistoricalPerformance — tracks and queries worker performance history.

Blueprint §10:
    Every execution produces routing evidence.
    Record: task, candidate_workers, selected_worker, probabilities,
    execution_time, cost, verification_score, failure, retry, final_result.

    Then calculate: routing_success_rate, verification_success_rate,
    mean_latency, failure_rate, cost_per_success, task_class_performance.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class WorkerStats:
    """Aggregate statistics for a worker over time."""

    worker_id: str
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    avg_latency_ms: float = 0.0
    avg_cost: float = 0.0
    avg_verification_score: float = 0.0
    success_rate: float = 0.0
    last_seen: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "total_tasks": self.total_tasks,
            "successful_tasks": self.successful_tasks,
            "failed_tasks": self.failed_tasks,
            "avg_latency_ms": self.avg_latency_ms,
            "avg_cost": self.avg_cost,
            "avg_verification_score": self.avg_verification_score,
            "success_rate": self.success_rate,
            "last_seen": self.last_seen,
        }


@dataclass
class PerformanceEntry:
    """One historical performance record."""

    worker_id: str
    task_id: str
    task_type: str
    success: bool
    latency_ms: float = 0.0
    cost: float = 0.0
    verification_score: float = 0.0
    attempt: int = 1
    context_tokens: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


class HistoricalPerformance:
    """Tracks and queries worker performance history.

    Usage::

        perf = HistoricalPerformance()
        perf.record(PerformanceEntry(
            worker_id="qwen3b", task_id="T-1", task_type="implementation",
            success=True, latency_ms=2500, verification_score=0.95,
        ))
        stats = perf.get_worker_stats("qwen3b")
    """

    def __init__(self) -> None:
        self._entries: List[PerformanceEntry] = []

    def record(self, entry: PerformanceEntry) -> None:
        """Record a performance entry."""
        self._entries.append(entry)

    def get_worker_stats(self, worker_id: str) -> WorkerStats:
        """Get aggregate stats for a worker."""
        entries = [e for e in self._entries if e.worker_id == worker_id]
        if not entries:
            return WorkerStats(worker_id=worker_id)

        total = len(entries)
        successes = sum(1 for e in entries if e.success)
        latencies = [e.latency_ms for e in entries if e.latency_ms > 0]
        costs = [e.cost for e in entries if e.cost > 0]
        scores = [e.verification_score for e in entries if e.verification_score > 0]

        return WorkerStats(
            worker_id=worker_id,
            total_tasks=total,
            successful_tasks=successes,
            failed_tasks=total - successes,
            avg_latency_ms=sum(latencies) / max(1, len(latencies)),
            avg_cost=sum(costs) / max(1, len(costs)),
            avg_verification_score=sum(scores) / max(1, len(scores)),
            success_rate=successes / max(1, total),
            last_seen=entries[-1].timestamp,
        )

    def get_task_type_stats(self, task_type: str) -> Dict[str, Any]:
        """Get aggregate stats for a task type."""
        entries = [e for e in self._entries if e.task_type == task_type]
        total = len(entries)
        successes = sum(1 for e in entries if e.success)
        return {
            "task_type": task_type,
            "total": total,
            "successes": successes,
            "success_rate": successes / max(1, total),
        }

    def get_recent(self, n: int = 10) -> List[PerformanceEntry]:
        """Get the most recent n entries."""
        return self._entries[-n:]

    def save(self, path: str) -> None:
        """Persist history to a JSON file."""
        data = [
            {
                "worker_id": e.worker_id,
                "task_id": e.task_id,
                "task_type": e.task_type,
                "success": e.success,
                "latency_ms": e.latency_ms,
                "cost": e.cost,
                "verification_score": e.verification_score,
                "attempt": e.attempt,
                "context_tokens": e.context_tokens,
                "timestamp": e.timestamp,
            }
            for e in self._entries
        ]
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self, path: str) -> None:
        """Load history from a JSON file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self._entries = [PerformanceEntry(**entry) for entry in data]
