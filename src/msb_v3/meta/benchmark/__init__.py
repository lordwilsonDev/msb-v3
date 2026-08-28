"""MultiWorkerBenchmark — run the same task across workers and compare outcomes.

Blueprint §10, §12 (META-8):
    Run the same task with Qwen, DeepSeek, Claude, Gemini, Google Skill.
    Swap workers without changing the project.
    Measure: success rate, first-attempt success, verification score,
    context tokens, tool calls, latency, repair count, cost.

The benchmark produces a BenchmarkResult that ranks workers by verified
outcome — not by model size or hype.

Usage::

    benchmark = MultiWorkerBenchmark(
        workers={
            "qwen3b": qwen3b_call,
            "qwen8b": qwen8b_call,
            "google-dev-knowledge": google_skill_call,
        },
        outcome_ledger=ledger,
    )
    result = benchmark.run(task)
    print(result.ranking)  # [("qwen8b", 0.95), ("qwen3b", 0.82), ...]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.meta.contracts import MetaTask
from msb_v3.meta.outcome.ledger import OutcomeLedger, PipelineOutcome
from msb_v3.meta.pipeline import FinalResult, MetaPipeline, ModelCall

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WorkerBenchmark:
    """One worker's result on a benchmark task."""

    worker_id: str
    verdict: str  # PASS / FAIL / EXPECTED_SKIP
    verification_score: float = 0.0
    attempts: int = 0
    latency_ms: float = 0.0
    mode_used: str = ""
    checks_passed: int = 0
    checks_run: int = 0
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "verdict": self.verdict,
            "verification_score": self.verification_score,
            "attempts": self.attempts,
            "latency_ms": self.latency_ms,
            "mode_used": self.mode_used,
            "checks_passed": self.checks_passed,
            "checks_run": self.checks_run,
            "error": self.error,
        }


@dataclass
class BenchmarkResult:
    """Comparative result of running the same task across multiple workers."""

    task_id: str
    task_objective: str
    workers: List[WorkerBenchmark] = field(default_factory=list)
    ranking: List[tuple[str, float]] = field(default_factory=list)
    started_at: str = field(default_factory=_now)
    completed_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_objective": self.task_objective[:200],
            "workers": [w.to_dict() for w in self.workers],
            "ranking": self.ranking,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @property
    def best_worker(self) -> Optional[WorkerBenchmark]:
        """The highest-ranked worker that passed."""
        for wb in self.workers:
            if wb.passed:
                return wb
        return None

    @property
    def pass_rate(self) -> float:
        """Fraction of workers that passed."""
        if not self.workers:
            return 0.0
        return sum(1 for w in self.workers if w.passed) / len(self.workers)

    @property
    def fastest_pass(self) -> Optional[WorkerBenchmark]:
        """The fastest worker that passed."""
        passes = [w for w in self.workers if w.passed]
        if not passes:
            return None
        return min(passes, key=lambda w: w.latency_ms)


class MultiWorkerBenchmark:
    """Run the same task across multiple workers and compare outcomes.

    Usage::

        benchmark = MultiWorkerBenchmark(
            workers={
                "qwen3b": lambda prompt: "def answer():\n    return 42\n",
                "qwen8b": lambda prompt: "def answer():\n    return 42\n",
            },
            workdir=Path("/tmp/benchmark"),
        )
        result = benchmark.run(task)
    """

    def __init__(
        self,
        *,
        workers: Dict[str, ModelCall],
        workdir: Optional[Path] = None,
        outcome_ledger: Optional[OutcomeLedger] = None,
        max_attempts: int = 3,
    ) -> None:
        self._workers = workers
        self._workdir = workdir or Path("/tmp/meta-benchmark")
        self._ledger = outcome_ledger
        self._max_attempts = max_attempts

    def run(
        self,
        task: MetaTask,
        *,
        worker_ids: Optional[List[str]] = None,
    ) -> BenchmarkResult:
        """Run the benchmark on *task* across all (or selected) workers.

        Args:
            task: The task to benchmark.
            worker_ids: If provided, only benchmark these workers.
        """
        target_workers = {
            wid: call
            for wid, call in self._workers.items()
            if worker_ids is None or wid in worker_ids
        }

        result = BenchmarkResult(
            task_id=task.task_id,
            task_objective=task.objective,
        )

        for worker_id, worker_call in target_workers.items():
            wb = self._benchmark_worker(task, worker_id, worker_call)
            result.workers.append(wb)

        # Rank by verification score (desc), then by latency (asc).
        result.ranking = self._rank(result.workers)
        result.completed_at = _now()

        return result

    def run_batch(
        self,
        tasks: List[MetaTask],
    ) -> List[BenchmarkResult]:
        """Run benchmarks on multiple tasks."""
        return [self.run(task) for task in tasks]

    def compare(
        self,
        results: List[BenchmarkResult],
    ) -> Dict[str, Any]:
        """Aggregate multiple benchmark results into a comparison summary."""
        if not results:
            return {"total_tasks": 0}

        worker_scores: Dict[str, List[float]] = {}
        worker_latencies: Dict[str, List[float]] = {}
        worker_passes: Dict[str, int] = {}
        worker_totals: Dict[str, int] = {}

        for result in results:
            for wb in result.workers:
                worker_scores.setdefault(wb.worker_id, []).append(
                    wb.verification_score
                )
                if wb.latency_ms > 0:
                    worker_latencies.setdefault(wb.worker_id, []).append(
                        wb.latency_ms
                    )
                worker_totals[wb.worker_id] = (
                    worker_totals.get(wb.worker_id, 0) + 1
                )
                if wb.passed:
                    worker_passes[wb.worker_id] = (
                        worker_passes.get(wb.worker_id, 0) + 1
                    )

        summaries = []
        for wid in sorted(worker_totals.keys()):
            scores = worker_scores.get(wid, [])
            lats = worker_latencies.get(wid, [])
            totals = worker_totals[wid]
            passes = worker_passes.get(wid, 0)
            summaries.append({
                "worker_id": wid,
                "tasks_run": totals,
                "tasks_passed": passes,
                "success_rate": passes / max(1, totals),
                "avg_verification_score": (
                    sum(scores) / len(scores) if scores else 0.0
                ),
                "avg_latency_ms": sum(lats) / len(lats) if lats else 0.0,
            })

        # Sort by success_rate desc, then avg_verification_score desc.
        summaries.sort(
            key=lambda s: (s["success_rate"], s["avg_verification_score"]),
            reverse=True,
        )

        return {
            "total_tasks": len(results),
            "unique_workers": list(worker_totals.keys()),
            "worker_summaries": summaries,
            "overall_pass_rate": sum(
                1 for r in results for w in r.workers if w.passed
            ) / max(1, sum(len(r.workers) for r in results)),
        }

    # ── Internal ────────────────────────────────────────────────────

    def _benchmark_worker(
        self,
        task: MetaTask,
        worker_id: str,
        worker_call: ModelCall,
    ) -> WorkerBenchmark:
        """Run one worker on the task and capture the result."""
        wb = WorkerBenchmark(worker_id=worker_id, verdict="FAIL")

        try:
            pipeline = MetaPipeline(
                worker=worker_call,
                workdir=self._workdir / task.task_id,
            )
            result: FinalResult = pipeline.run(
                task,
                max_attempts=self._max_attempts,
                worker_id=worker_id,
            )

            wb.verdict = result.verdict.value
            wb.verification_score = result.gate.confidence
            wb.attempts = result.metadata.get("attempts", 1)
            wb.mode_used = result.policy.mode.value
            wb.checks_passed = result.gate.checks_passed
            wb.checks_run = result.gate.checks_run

            # Compute latency from stages.
            wb.latency_ms = sum(s.duration_ms for s in result.stages)

            # Capture error reason from gate on failure.
            if not result.passed and result.gate.reason:
                wb.error = result.gate.reason

        except Exception as exc:  # noqa: BLE001
            wb.verdict = "FAIL"
            wb.error = f"{type(exc).__name__}: {exc}"
            logger.warning("benchmark worker %s failed: %s", worker_id, exc)

        # Record to ledger.
        if self._ledger is not None:
            self._record_to_ledger(task, wb)

        return wb

    def _record_to_ledger(
        self,
        task: MetaTask,
        wb: WorkerBenchmark,
    ) -> None:
        """Feed one worker's benchmark result to the outcome ledger.

        Called only when self._ledger is not None (see _benchmark_worker).
        """
        if self._ledger is None:  # mypy narrowing
            return
        outcome = PipelineOutcome(
            task_id=task.task_id,
            task_objective=task.objective[:200],
            task_type=task.metadata.get("task_type", "benchmark"),
            worker_id=wb.worker_id,
            execution_mode=wb.mode_used,
            verdict=wb.verdict,
            verification_score=wb.verification_score,
            attempts=wb.attempts,
            latency_ms=wb.latency_ms,
            failure_class="",
            failure_message=wb.error,
            metadata={"benchmark": True},
        )
        self._ledger.record(outcome)

    @staticmethod
    def _rank(workers: List[WorkerBenchmark]) -> List[tuple[str, float]]:
        """Rank workers: PASS first (by score desc), then FAIL (by score desc)."""
        passes = sorted(
            [w for w in workers if w.passed],
            key=lambda w: w.verification_score,
            reverse=True,
        )
        fails = sorted(
            [w for w in workers if not w.passed],
            key=lambda w: w.verification_score,
            reverse=True,
        )
        return [(w.worker_id, w.verification_score) for w in passes + fails]
