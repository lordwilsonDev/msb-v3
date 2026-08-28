"""OutcomeLedger — records every pipeline execution and feeds the probability engine.

Blueprint §10 (Routing Memory):
    Every execution produces routing evidence.
    Record: task, candidate_workers, selected_worker, probabilities,
    execution_time, cost, verification_score, failure, retry, final_result.

    Then calculate: routing_success_rate, verification_success_rate,
    mean_latency, failure_rate, cost_per_success, task_class_performance.

The OutcomeLedger is the bridge between pipeline execution and adaptive learning.
It captures one PipelineOutcome per run, persists it as an append-only JSONL audit
trail, and optionally feeds verified results into RoutingMatrix and
HistoricalPerformance so the probability engine improves from real outcomes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.meta.probability.historical_performance import (
    HistoricalPerformance,
    PerformanceEntry,
)
from msb_v3.meta.probability.routing_matrix import (
    RoutingMatrix,
    RoutingObservation,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PipelineOutcome:
    """One complete pipeline execution record.

    Captures everything needed to reconstruct what happened, feed the
    probability engine, and answer routing-memory queries.
    """

    task_id: str
    task_objective: str
    task_type: str
    worker_id: str
    execution_mode: str  # FABLE / HYBRID / LOCAL

    # Outcome.
    verdict: str  # PASS / FAIL / EXPECTED_SKIP
    verification_score: float = 0.0
    attempts: int = 1

    # Performance.
    latency_ms: float = 0.0
    cost: float = 0.0
    context_tokens: int = 0

    # Routing context.
    candidate_workers: List[str] = field(default_factory=list)
    probabilities: Dict[str, float] = field(default_factory=dict)

    # Failure.
    failure_class: str = ""
    failure_message: str = ""
    escalation_triggered: bool = False

    # Audit.
    timestamp: str = field(default_factory=_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for JSONL persistence."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PipelineOutcome:
        """Deserialize from JSONL record."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class OutcomeLedger:
    """Records pipeline executions and feeds the probability engine.

    Usage::

        ledger = OutcomeLedger(
            workdir=Path("/tmp/meta-outcomes"),
            routing_matrix=routing_matrix,
            performance=historical_performance,
        )

        outcome = PipelineOutcome(
            task_id="T-1",
            task_objective="Implement answer()",
            task_type="implementation",
            worker_id="qwen3b",
            execution_mode="HYBRID",
            verdict="PASS",
            verification_score=0.95,
            latency_ms=2500,
        )
        ledger.record(outcome)

        # Query routing memory.
        stats = ledger.worker_stats("qwen3b")
    """

    def __init__(
        self,
        *,
        workdir: Optional[Path] = None,
        routing_matrix: Optional[RoutingMatrix] = None,
        performance: Optional[HistoricalPerformance] = None,
    ) -> None:
        self._workdir = workdir
        self._matrix = routing_matrix
        self._performance = performance
        self._outcomes: List[PipelineOutcome] = []
        self._ledger_file: Optional[Path] = None

        if workdir is not None:
            workdir.mkdir(parents=True, exist_ok=True)
            self._ledger_file = workdir / "outcome-ledger.jsonl"

    def record(self, outcome: PipelineOutcome) -> None:
        """Record a pipeline outcome and feed probability engines.

        This is the primary entry point. It:
        1. Appends to the in-memory ledger.
        2. Persists to JSONL (if workdir configured).
        3. Feeds RoutingMatrix (if configured).
        4. Feeds HistoricalPerformance (if configured).
        """
        self._outcomes.append(outcome)

        # Persist to JSONL.
        if self._ledger_file is not None:
            with self._ledger_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(outcome.to_dict()) + "\n")
            logger.debug("recorded outcome to %s", self._ledger_file)

        # Feed RoutingMatrix.
        if self._matrix is not None:
            self._matrix.record(RoutingObservation(
                worker_id=outcome.worker_id,
                task_type=outcome.task_type,
                success=outcome.verdict == "PASS",
                verification_score=outcome.verification_score,
                latency_ms=outcome.latency_ms,
                cost=outcome.cost,
                context_tokens=outcome.context_tokens,
                attempt=outcome.attempts,
                metadata={
                    "task_id": outcome.task_id,
                    "execution_mode": outcome.execution_mode,
                },
            ))

        # Feed HistoricalPerformance.
        if self._performance is not None:
            self._performance.record(PerformanceEntry(
                worker_id=outcome.worker_id,
                task_id=outcome.task_id,
                task_type=outcome.task_type,
                success=outcome.verdict == "PASS",
                latency_ms=outcome.latency_ms,
                cost=outcome.cost,
                verification_score=outcome.verification_score,
                attempt=outcome.attempts,
                context_tokens=outcome.context_tokens,
                metadata={
                    "execution_mode": outcome.execution_mode,
                    "failure_class": outcome.failure_class,
                },
            ))

    def load_from_disk(self) -> int:
        """Replay the JSONL ledger into memory. Returns number of records loaded."""
        if self._ledger_file is None or not self._ledger_file.exists():
            return 0

        count = 0
        with self._ledger_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    outcome = PipelineOutcome.from_dict(data)
                    self._outcomes.append(outcome)
                    count += 1
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("skipping malformed ledger line: %s", exc)
        return count

    def recent(self, n: int = 10) -> List[PipelineOutcome]:
        """Get the most recent n outcomes."""
        return self._outcomes[-n:]

    def query(
        self,
        *,
        worker_id: Optional[str] = None,
        task_type: Optional[str] = None,
        verdict: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> List[PipelineOutcome]:
        """Query outcomes by filters."""
        results = self._outcomes
        if worker_id is not None:
            results = [o for o in results if o.worker_id == worker_id]
        if task_type is not None:
            results = [o for o in results if o.task_type == task_type]
        if verdict is not None:
            results = [o for o in results if o.verdict == verdict]
        if mode is not None:
            results = [o for o in results if o.execution_mode == mode]
        return results

    def worker_stats(self, worker_id: str) -> Dict[str, Any]:
        """Get aggregate stats for a worker from ledger outcomes."""
        outcomes = [o for o in self._outcomes if o.worker_id == worker_id]
        if not outcomes:
            return {"worker_id": worker_id, "total": 0}

        total = len(outcomes)
        passes = sum(1 for o in outcomes if o.verdict == "PASS")
        latencies = [o.latency_ms for o in outcomes if o.latency_ms > 0]

        return {
            "worker_id": worker_id,
            "total": total,
            "passes": passes,
            "failures": total - passes,
            "success_rate": passes / max(1, total),
            "avg_latency_ms": (
                sum(latencies) / len(latencies) if latencies else 0.0
            ),
            "modes_used": list({o.execution_mode for o in outcomes}),
            "task_types": list({o.task_type for o in outcomes}),
        }

    def task_type_stats(self, task_type: str) -> Dict[str, Any]:
        """Get aggregate stats for a task type from ledger outcomes."""
        outcomes = [o for o in self._outcomes if o.task_type == task_type]
        if not outcomes:
            return {"task_type": task_type, "total": 0}

        total = len(outcomes)
        passes = sum(1 for o in outcomes if o.verdict == "PASS")
        workers = list({o.worker_id for o in outcomes})
        modes = list({o.execution_mode for o in outcomes})

        return {
            "task_type": task_type,
            "total": total,
            "passes": passes,
            "success_rate": passes / max(1, total),
            "workers_tried": workers,
            "modes_used": modes,
        }

    def summary(self) -> Dict[str, Any]:
        """Get a full summary of all recorded outcomes."""
        if not self._outcomes:
            return {"total": 0}

        total = len(self._outcomes)
        passes = sum(1 for o in self._outcomes if o.verdict == "PASS")
        latencies = [o.latency_ms for o in self._outcomes if o.latency_ms > 0]
        workers = list({o.worker_id for o in self._outcomes})
        task_types = list({o.task_type for o in self._outcomes})
        modes = list({o.execution_mode for o in self._outcomes})

        return {
            "total": total,
            "passes": passes,
            "failures": total - passes,
            "success_rate": passes / max(1, total),
            "avg_latency_ms": (
                sum(latencies) / len(latencies) if latencies else 0.0
            ),
            "unique_workers": workers,
            "unique_task_types": task_types,
            "unique_modes": modes,
            "escalations": sum(1 for o in self._outcomes if o.escalation_triggered),
        }

    @property
    def count(self) -> int:
        return len(self._outcomes)
