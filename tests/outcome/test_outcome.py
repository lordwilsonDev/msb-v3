"""META-5: OutcomeLedger conformance tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from msb_v3.meta.outcome.ledger import OutcomeLedger, PipelineOutcome
from msb_v3.meta.probability.historical_performance import HistoricalPerformance
from msb_v3.meta.probability.routing_matrix import RoutingMatrix
from msb_v3.meta.verification.gate import VerificationGate

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _outcome(
    *,
    task_id: str = "T-1",
    worker_id: str = "qwen3b",
    task_type: str = "implementation",
    mode: str = "HYBRID",
    verdict: str = "PASS",
    score: float = 0.9,
    latency_ms: float = 2000,
) -> PipelineOutcome:
    return PipelineOutcome(
        task_id=task_id,
        task_objective=f"Test objective for {task_id}",
        task_type=task_type,
        worker_id=worker_id,
        execution_mode=mode,
        verdict=verdict,
        verification_score=score,
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# In-memory recording
# ---------------------------------------------------------------------------

class TestInMemoryRecording:
    def test_record_single(self) -> None:
        ledger = OutcomeLedger()
        ledger.record(_outcome())
        assert ledger.count == 1

    def test_record_multiple(self) -> None:
        ledger = OutcomeLedger()
        for i in range(5):
            ledger.record(_outcome(task_id=f"T-{i}"))
        assert ledger.count == 5

    def test_recent(self) -> None:
        ledger = OutcomeLedger()
        for i in range(10):
            ledger.record(_outcome(task_id=f"T-{i}"))
        recent = ledger.recent(3)
        assert len(recent) == 3
        assert recent[0].task_id == "T-7"

    def test_recent_empty(self) -> None:
        ledger = OutcomeLedger()
        assert ledger.recent() == []


# ---------------------------------------------------------------------------
# JSONL persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_record_writes_jsonl(self, tmp_path: Path) -> None:
        ledger = OutcomeLedger(workdir=tmp_path)
        ledger.record(_outcome())
        ledger.record(_outcome(task_id="T-2"))

        ledger_file = tmp_path / "outcome-ledger.jsonl"
        assert ledger_file.exists()
        lines = ledger_file.read_text().strip().split("\n")
        assert len(lines) == 2

        first = json.loads(lines[0])
        assert first["task_id"] == "T-1"
        assert first["verdict"] == "PASS"

    def test_load_from_disk(self, tmp_path: Path) -> None:
        ledger = OutcomeLedger(workdir=tmp_path)
        ledger.record(_outcome(task_id="T-1"))
        ledger.record(_outcome(task_id="T-2"))
        ledger.record(_outcome(task_id="T-3"))

        # Create a fresh ledger and load.
        ledger2 = OutcomeLedger(workdir=tmp_path)
        count = ledger2.load_from_disk()
        assert count == 3
        assert ledger2.count == 3
        assert ledger2.recent(1)[0].task_id == "T-3"

    def test_load_empty_file(self, tmp_path: Path) -> None:
        ledger_file = tmp_path / "outcome-ledger.jsonl"
        ledger_file.write_text("")
        ledger = OutcomeLedger(workdir=tmp_path)
        count = ledger.load_from_disk()
        assert count == 0

    def test_load_malformed_lines(self, tmp_path: Path) -> None:
        ledger_file = tmp_path / "outcome-ledger.jsonl"
        ledger_file.write_text("not json\n{\"task_id\":\"T-1\",\"task_objective\":\"x\",\"task_type\":\"impl\",\"worker_id\":\"w\",\"execution_mode\":\"LOCAL\",\"verdict\":\"PASS\"}\n")
        ledger = OutcomeLedger(workdir=tmp_path)
        count = ledger.load_from_disk()
        assert count == 1  # malformed line skipped


# ---------------------------------------------------------------------------
# RoutingMatrix feeding
# ---------------------------------------------------------------------------

class TestRoutingMatrixFeeding:
    def test_feeds_matrix_on_record(self) -> None:
        matrix = RoutingMatrix()
        ledger = OutcomeLedger(routing_matrix=matrix)

        ledger.record(_outcome(verdict="PASS"))
        ledger.record(_outcome(task_id="T-2", verdict="FAIL"))
        ledger.record(_outcome(task_id="T-3", verdict="PASS"))

        prob = matrix.get_probability("qwen3b", "implementation")
        # 2 passes out of 3 → (2+1)/(3+2) = 0.6 with alpha=1
        assert abs(prob - 0.6) < 0.01

    def test_matrix_receives_observation_metadata(self) -> None:
        matrix = RoutingMatrix()
        ledger = OutcomeLedger(routing_matrix=matrix)
        ledger.record(_outcome(mode="LOCAL"))

        assert len(matrix._observations) == 1
        obs = matrix._observations[0]
        assert obs.metadata["execution_mode"] == "LOCAL"
        assert obs.metadata["task_id"] == "T-1"


# ---------------------------------------------------------------------------
# HistoricalPerformance feeding
# ---------------------------------------------------------------------------

class TestPerformanceFeeding:
    def test_feeds_performance_on_record(self) -> None:
        perf = HistoricalPerformance()
        ledger = OutcomeLedger(performance=perf)

        ledger.record(_outcome(latency_ms=1000))
        ledger.record(_outcome(task_id="T-2", latency_ms=3000, verdict="FAIL"))

        stats = perf.get_worker_stats("qwen3b")
        assert stats.total_tasks == 2
        assert stats.successful_tasks == 1
        assert stats.avg_latency_ms == 2000.0


# ---------------------------------------------------------------------------
# Query interface
# ---------------------------------------------------------------------------

class TestQuery:
    def test_query_by_worker(self) -> None:
        ledger = OutcomeLedger()
        ledger.record(_outcome(worker_id="qwen3b"))
        ledger.record(_outcome(task_id="T-2", worker_id="deepseek"))
        ledger.record(_outcome(task_id="T-3", worker_id="qwen3b"))

        results = ledger.query(worker_id="qwen3b")
        assert len(results) == 2

    def test_query_by_task_type(self) -> None:
        ledger = OutcomeLedger()
        ledger.record(_outcome(task_type="implementation"))
        ledger.record(_outcome(task_id="T-2", task_type="research"))
        ledger.record(_outcome(task_id="T-3", task_type="implementation"))

        results = ledger.query(task_type="research")
        assert len(results) == 1
        assert results[0].task_id == "T-2"

    def test_query_by_verdict(self) -> None:
        ledger = OutcomeLedger()
        ledger.record(_outcome(verdict="PASS"))
        ledger.record(_outcome(task_id="T-2", verdict="FAIL"))
        ledger.record(_outcome(task_id="T-3", verdict="PASS"))

        results = ledger.query(verdict="FAIL")
        assert len(results) == 1

    def test_query_by_mode(self) -> None:
        ledger = OutcomeLedger()
        ledger.record(_outcome(mode="FABLE"))
        ledger.record(_outcome(task_id="T-2", mode="LOCAL"))
        ledger.record(_outcome(task_id="T-3", mode="FABLE"))

        results = ledger.query(mode="LOCAL")
        assert len(results) == 1

    def test_query_combined(self) -> None:
        ledger = OutcomeLedger()
        ledger.record(_outcome(worker_id="qwen3b", verdict="PASS", task_type="coding"))
        ledger.record(_outcome(task_id="T-2", worker_id="qwen3b", verdict="FAIL", task_type="coding"))
        ledger.record(_outcome(task_id="T-3", worker_id="deepseek", verdict="PASS", task_type="coding"))

        results = ledger.query(worker_id="qwen3b", verdict="PASS")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Aggregate stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_worker_stats(self) -> None:
        ledger = OutcomeLedger()
        ledger.record(_outcome(worker_id="qwen3b", latency_ms=1000))
        ledger.record(_outcome(task_id="T-2", worker_id="qwen3b", verdict="FAIL", latency_ms=3000))
        ledger.record(_outcome(task_id="T-3", worker_id="deepseek", latency_ms=500))

        stats = ledger.worker_stats("qwen3b")
        assert stats["total"] == 2
        assert stats["passes"] == 1
        assert stats["success_rate"] == 0.5
        assert stats["avg_latency_ms"] == 2000.0

    def test_worker_stats_empty(self) -> None:
        ledger = OutcomeLedger()
        stats = ledger.worker_stats("nonexistent")
        assert stats["total"] == 0

    def test_task_type_stats(self) -> None:
        ledger = OutcomeLedger()
        ledger.record(_outcome(task_type="coding"))
        ledger.record(_outcome(task_id="T-2", task_type="coding", verdict="FAIL"))
        ledger.record(_outcome(task_id="T-3", task_type="research"))

        stats = ledger.task_type_stats("coding")
        assert stats["total"] == 2
        assert stats["success_rate"] == 0.5
        assert "qwen3b" in stats["workers_tried"]

    def test_summary(self) -> None:
        ledger = OutcomeLedger()
        ledger.record(_outcome())
        ledger.record(_outcome(task_id="T-2", verdict="FAIL"))
        ledger.record(_outcome(task_id="T-3", mode="LOCAL"))

        s = ledger.summary()
        assert s["total"] == 3
        assert s["passes"] == 2
        assert s["success_rate"] == pytest.approx(2 / 3, abs=0.01)
        assert "qwen3b" in s["unique_workers"]
        assert len(s["unique_modes"]) == 2

    def test_summary_empty(self) -> None:
        ledger = OutcomeLedger()
        s = ledger.summary()
        assert s["total"] == 0


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_to_dict_and_from_dict(self) -> None:
        original = _outcome()
        data = original.to_dict()
        restored = PipelineOutcome.from_dict(data)
        assert restored.task_id == original.task_id
        assert restored.worker_id == original.worker_id
        assert restored.verdict == original.verdict
        assert restored.execution_mode == original.execution_mode


# ---------------------------------------------------------------------------
# Pipeline integration (meta-pipeline records automatically)
# ---------------------------------------------------------------------------

class TestPipelineIntegration:
    def test_pipeline_records_outcome(self, tmp_path: Path) -> None:
        from msb_v3.meta.contracts import MetaTask
        from msb_v3.meta.pipeline import MetaPipeline

        ledger = OutcomeLedger(workdir=tmp_path)
        pipeline = MetaPipeline(
            worker=lambda prompt: "def answer():\n    return 42\n",
            workdir=tmp_path / "pipeline",
            outcome_ledger=ledger,
        )

        result = pipeline.run(MetaTask(
            task_id="T-INT",
            objective="Implement answer() that returns 42",
            metadata={
                "verification_commands": [
                    "python -c \"exec(open('artifact.py').read()); assert answer() == 42\""
                ],
            },
        ))

        assert result.passed
        assert ledger.count == 1
        outcome = ledger.recent(1)[0]
        assert outcome.task_id == "T-INT"
        assert outcome.verdict == "PASS"
        assert outcome.execution_mode in ("FABLE", "HYBRID", "LOCAL")

    def test_pipeline_records_failure(self, tmp_path: Path) -> None:
        from msb_v3.meta.contracts import MetaTask
        from msb_v3.meta.pipeline import MetaPipeline

        ledger = OutcomeLedger(workdir=tmp_path)
        pipeline = MetaPipeline(
            worker=lambda prompt: "def wrong():\n    return 99\n",
            workdir=tmp_path / "pipeline",
            outcome_ledger=ledger,
            verification_gate=VerificationGate(),
        )

        result = pipeline.run(MetaTask(
            task_id="T-FAIL",
            objective="Implement answer() that returns 42",
            metadata={
                "verification_commands": [
                    "python -c \"exec(open('artifact.py').read()); assert answer() == 42\""
                ],
            },
        ))

        assert not result.passed
        assert ledger.count == 1
        outcome = ledger.recent(1)[0]
        assert outcome.verdict == "FAIL"
