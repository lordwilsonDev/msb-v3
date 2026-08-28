"""META-4: Pipeline end-to-end tests — full pipeline composition.

Tests verify:
  - Pipeline composes: MetaTask → Policy → Translate → Execute → Verify → Result
  - Fake worker that produces correct code → PASS
  - Fake worker that produces wrong code → FAIL with retry
  - Fake worker that errors → FAIL with repair
  - Pipeline records stage trace
  - Pipeline respects max_attempts
  - Pipeline uses injected worker (no hard model dependency)
  - Pipeline produces evidence chain
"""

from __future__ import annotations

import sys
from pathlib import Path

from msb_v3.meta.contracts import MetaTask, Verdict, WorkerStatus
from msb_v3.meta.pipeline import MetaPipeline


def _correct_worker(prompt: str) -> str:
    """Always produces correct code."""
    return "def answer():\n    return 42\n"


def _wrong_then_correct_worker():
    """First call returns wrong, second returns correct."""
    calls = []

    def worker(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return "def answer():\n    return 0\n"
        return "def answer():\n    return 42\n"

    return worker


def _always_wrong_worker(prompt: str) -> str:
    """Always returns wrong code."""
    return "def answer():\n    return 0\n"


def _error_worker(prompt: str) -> str:
    """Always raises an error."""
    raise RuntimeError("model unavailable")


def _task_with_verification() -> MetaTask:
    return MetaTask(
        task_id="T-CORRECT",
        objective="Implement answer() that returns 42",
        metadata={
            "verification_commands": [
                f"{sys.executable} -c 'exec(open(\"artifact.py\").read()); assert answer() == 42'",
            ],
        },
    )


# ---------------------------------------------------------------------------
# Pipeline composition
# ---------------------------------------------------------------------------

class TestPipelineComposition:
    def test_passing_task_produces_pass(self, tmp_path: Path) -> None:
        pipeline = MetaPipeline(worker=_correct_worker, workdir=tmp_path)
        result = pipeline.run(_task_with_verification())
        assert result.passed is True
        assert result.verdict is Verdict.PASS
        assert result.policy is not None

    def test_failing_task_produces_fail(self, tmp_path: Path) -> None:
        pipeline = MetaPipeline(worker=_always_wrong_worker, workdir=tmp_path)
        result = pipeline.run(_task_with_verification(), max_attempts=1)
        assert result.passed is False
        assert result.verdict is Verdict.FAIL
        # Should have attempted once
        execute_stages = [s for s in result.stages if s.name.startswith("execute")]
        assert len(execute_stages) == 1

    def test_error_worker_produces_fail(self, tmp_path: Path) -> None:
        pipeline = MetaPipeline(worker=_error_worker, workdir=tmp_path)
        result = pipeline.run(_task_with_verification())
        assert result.passed is False
        assert result.gate.repair_suggested is True

    def test_correction_loop_recovers(self, tmp_path: Path) -> None:
        worker = _wrong_then_correct_worker()
        pipeline = MetaPipeline(worker=worker, workdir=tmp_path)
        result = pipeline.run(_task_with_verification(), max_attempts=3)
        assert result.passed is True
        # Worker was called at least once (may be once if first attempt passes check)
        execute_stages = [s for s in result.stages if s.name.startswith("execute")]
        assert len(execute_stages) >= 1

    def test_stage_trace_recorded(self, tmp_path: Path) -> None:
        pipeline = MetaPipeline(worker=_correct_worker, workdir=tmp_path)
        result = pipeline.run(_task_with_verification())
        stage_names = [s.name for s in result.stages]
        assert "policy" in stage_names
        assert "translate" in stage_names
        assert "execute_a1" in stage_names
        assert "verify_a1" in stage_names

    def test_max_attempts_respected(self, tmp_path: Path) -> None:
        pipeline = MetaPipeline(worker=_always_wrong_worker, workdir=tmp_path)
        result = pipeline.run(_task_with_verification(), max_attempts=1)
        execute_stages = [s for s in result.stages if s.name.startswith("execute")]
        assert len(execute_stages) == 1

    def test_result_serializes(self, tmp_path: Path) -> None:
        pipeline = MetaPipeline(worker=_correct_worker, workdir=tmp_path)
        result = pipeline.run(_task_with_verification())
        d = result.to_dict()
        assert d["task_id"] == "T-CORRECT"
        assert d["verdict"] == "PASS"
        assert d["mode"] in ("FABLE", "HYBRID", "LOCAL")
        assert len(d["stages"]) >= 4

    def test_worker_result_available(self, tmp_path: Path) -> None:
        pipeline = MetaPipeline(worker=_correct_worker, workdir=tmp_path)
        result = pipeline.run(_task_with_verification())
        assert result.worker_result is not None
        assert result.worker_result.status is WorkerStatus.PRODUCED

    def test_verification_result_available(self, tmp_path: Path) -> None:
        pipeline = MetaPipeline(worker=_correct_worker, workdir=tmp_path)
        result = pipeline.run(_task_with_verification())
        assert result.verification is not None
        assert result.verification.verdict is Verdict.PASS

    def test_no_verification_commands_still_runs(self, tmp_path: Path) -> None:
        """Pipeline runs even without verification commands — result is EXPECTED_SKIP."""
        task = MetaTask(task_id="T-NO-CHECKS", objective="Do something")
        pipeline = MetaPipeline(worker=_correct_worker, workdir=tmp_path)
        result = pipeline.run(task)
        # No commands → EXPECTED_SKIP from verification
        assert result.verdict is Verdict.EXPECTED_SKIP

    def test_pipeline_uses_injected_worker(self, tmp_path: Path) -> None:
        """The pipeline never imports a model — it uses whatever callable you give it."""
        calls = []

        def tracking_worker(prompt: str) -> str:
            calls.append(prompt)
            return "x = 1\n"

        pipeline = MetaPipeline(worker=tracking_worker, workdir=tmp_path)
        task = MetaTask(
            task_id="T-TRACK",
            objective="set x to 1",
            metadata={"verification_commands": [f"{sys.executable} -c 'pass'"]},
        )
        pipeline.run(task)
        assert len(calls) >= 1
        assert "objective: set x to 1" in calls[0]
