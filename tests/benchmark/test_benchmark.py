"""META-8: MultiWorkerBenchmark — cross-worker comparison.

Tests that:
  1. Benchmark runs same task across multiple workers
  2. Results are ranked by verification score
  3. Batch comparison aggregates across tasks
  4. Ledger integration records all worker outcomes
  5. Edge cases: single worker, all fail, all pass
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from msb_v3.meta.benchmark import (
    BenchmarkResult,
    MultiWorkerBenchmark,
    WorkerBenchmark,
)
from msb_v3.meta.contracts import Complexity, MetaTask
from msb_v3.meta.outcome.ledger import OutcomeLedger

# ── Fixtures ─────────────────────────────────────────────────────────

# Verification command that checks artifact.py is valid Python with answer().
# Uses compile() — no shell quoting issues, works cross-platform.
_SYNTAX_CHECK = "python3 -c \"compile(open('artifact.py').read(), 'artifact.py', 'exec')\""


def _make_task(
    task_id: str = "bench-1",
    objective: str = "Implement answer() that returns 42",
    verification_cmd: str | None = None,
) -> MetaTask:
    cmd = verification_cmd or _SYNTAX_CHECK
    return MetaTask(
        task_id=task_id,
        objective=objective,
        task_type="implementation",
        complexity=Complexity.LOW,
        metadata={"verification_commands": [cmd]},
    )


def _passing_worker(prompt: str) -> str:
    """Produces valid Python that defines answer()."""
    return "def answer():\n    return 42\n"


def _failing_worker(prompt: str) -> str:
    """Produces invalid Python — fails syntax check."""
    return "THIS IS NOT VALID PYTHON"


def _broken_worker(prompt: str) -> str:
    raise RuntimeError("worker exploded")


def _run_benchmark(workers, task=None, run_kw=None, **bench_kw):
    """Run a benchmark with isolated temp directory."""
    task = task or _make_task()
    run_kw = run_kw or {}
    with tempfile.TemporaryDirectory() as tmpdir:
        benchmark = MultiWorkerBenchmark(
            workers=workers,
            workdir=Path(tmpdir),
            **bench_kw,
        )
        return benchmark.run(task, **run_kw), benchmark


# ── Basic benchmark ─────────────────────────────────────────────────


class TestBenchmarkBasic:
    def test_runs_across_two_workers(self) -> None:
        result, _ = _run_benchmark({
            "passer": _passing_worker,
            "failer": _failing_worker,
        })
        assert isinstance(result, BenchmarkResult)
        assert len(result.workers) == 2
        assert result.completed_at != ""

    def test_passing_worker_wins_ranking(self) -> None:
        result, _ = _run_benchmark({
            "passer": _passing_worker,
            "failer": _failing_worker,
        })
        assert result.ranking[0][0] == "passer"
        assert result.best_worker is not None
        assert result.best_worker.worker_id == "passer"

    def test_all_workers_recorded(self) -> None:
        result, _ = _run_benchmark({
            "passer": _passing_worker,
            "failer": _failing_worker,
        })
        worker_ids = {w.worker_id for w in result.workers}
        assert "passer" in worker_ids
        assert "failer" in worker_ids

    def test_ranking_orders_by_score(self) -> None:
        result, _ = _run_benchmark({
            "passer": _passing_worker,
            "failer": _failing_worker,
        })
        scores = {wid: score for wid, score in result.ranking}
        assert scores["passer"] >= scores["failer"]


# ── Filtering ────────────────────────────────────────────────────────


class TestBenchmarkFiltering:
    def test_select_specific_workers(self) -> None:
        result, _ = _run_benchmark({
            "passer": _passing_worker,
            "failer": _failing_worker,
        }, run_kw={"worker_ids": ["passer"]})
        assert len(result.workers) == 1
        assert result.workers[0].worker_id == "passer"


# ── Batch comparison ─────────────────────────────────────────────────


class TestBatchComparison:
    def test_compare_aggregates_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workers = {
                "passer": _passing_worker,
                "failer": _failing_worker,
            }
            benchmark = MultiWorkerBenchmark(
                workers=workers, workdir=Path(tmpdir),
            )
            r1 = benchmark.run(_make_task(task_id="t1"))
            r2 = benchmark.run(_make_task(task_id="t2"))
            summary = benchmark.compare([r1, r2])

            assert summary["total_tasks"] == 2
            assert "passer" in summary["unique_workers"]
            assert "failer" in summary["unique_workers"]
            assert len(summary["worker_summaries"]) == 2

    def test_compare_ranks_by_success_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workers = {
                "passer": _passing_worker,
                "failer": _failing_worker,
            }
            benchmark = MultiWorkerBenchmark(
                workers=workers, workdir=Path(tmpdir),
            )
            results = [benchmark.run(_make_task(task_id=f"t{i}")) for i in range(3)]
            summary = benchmark.compare(results)
            summaries = summary["worker_summaries"]
            assert summaries[0]["worker_id"] == "passer"
            assert summaries[0]["success_rate"] == 1.0
            assert summaries[1]["worker_id"] == "failer"
            assert summaries[1]["success_rate"] == 0.0

    def test_compare_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            benchmark = MultiWorkerBenchmark(
                workers={"w": _passing_worker}, workdir=Path(tmpdir),
            )
            summary = benchmark.compare([])
            assert summary["total_tasks"] == 0


# ── Ledger integration ──────────────────────────────────────────────


class TestLedgerIntegration:
    def test_records_all_workers_to_ledger(self) -> None:
        ledger = OutcomeLedger()
        with tempfile.TemporaryDirectory() as tmpdir:
            benchmark = MultiWorkerBenchmark(
                workers={
                    "passer": _passing_worker,
                    "failer": _failing_worker,
                },
                workdir=Path(tmpdir),
                outcome_ledger=ledger,
            )
            benchmark.run(_make_task())
        assert ledger.count == 2
        worker_ids = {o.worker_id for o in ledger.recent(10)}
        assert "passer" in worker_ids
        assert "failer" in worker_ids

    def test_ledger_records_benchmark_metadata(self) -> None:
        ledger = OutcomeLedger()
        with tempfile.TemporaryDirectory() as tmpdir:
            benchmark = MultiWorkerBenchmark(
                workers={"passer": _passing_worker},
                workdir=Path(tmpdir),
                outcome_ledger=ledger,
            )
            benchmark.run(_make_task())
        outcome = ledger.recent(1)[0]
        assert outcome.metadata.get("benchmark") is True


# ── Edge cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    def test_single_worker(self) -> None:
        result, _ = _run_benchmark({"solo": _passing_worker})
        assert len(result.workers) == 1
        assert len(result.ranking) == 1
        assert result.best_worker is not None

    def test_worker_exception_captured(self) -> None:
        result, _ = _run_benchmark({"crasher": _broken_worker})
        assert len(result.workers) == 1
        wb = result.workers[0]
        assert wb.verdict == "FAIL"
        assert "RuntimeError" in wb.error

    def test_pass_rate_calculation(self) -> None:
        result, _ = _run_benchmark({
            "p1": _passing_worker,
            "p2": _passing_worker,
            "f1": _failing_worker,
        })
        assert abs(result.pass_rate - 2 / 3) < 0.01

    def test_fastest_pass(self) -> None:
        result, _ = _run_benchmark({
            "fast": _passing_worker,
            "slow": _failing_worker,
        })
        fp = result.fastest_pass
        assert fp is not None
        assert fp.worker_id == "fast"

    def test_to_dict_serialization(self) -> None:
        result, _ = _run_benchmark({"passer": _passing_worker})
        d = result.to_dict()
        assert "task_id" in d
        assert "workers" in d
        assert "ranking" in d
        assert len(d["workers"]) == 1

    def test_empty_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            benchmark = MultiWorkerBenchmark(
                workers={"w": _passing_worker}, workdir=Path(tmpdir),
            )
            summary = benchmark.compare([])
            assert summary == {"total_tasks": 0}


# ── WorkerBenchmark data ────────────────────────────────────────────


class TestWorkerBenchmarkData:
    def test_passed_property(self) -> None:
        wb = WorkerBenchmark(worker_id="w1", verdict="PASS")
        assert wb.passed is True

    def test_not_passed(self) -> None:
        wb = WorkerBenchmark(worker_id="w1", verdict="FAIL")
        assert wb.passed is False

    def test_to_dict(self) -> None:
        wb = WorkerBenchmark(
            worker_id="w1",
            verdict="PASS",
            verification_score=0.95,
            latency_ms=1200,
        )
        d = wb.to_dict()
        assert d["worker_id"] == "w1"
        assert d["verdict"] == "PASS"
        assert d["verification_score"] == 0.95
