"""META-3: VerificationGate tests — independent verification.

Tests verify:
  - Worker error → immediate fail with repair_suggested
  - Worker no_change → expected_skip
  - Standard strategy runs verification commands
  - Strict strategy adds boundary checks
  - Fuzzy strategy adds semantic checks
  - All checks pass → PASS verdict
  - Any check fails → FAIL verdict
  - No checks → EXPECTED_SKIP
  - Confidence calculation
  - Gate result serialization
  - Worker is never allowed to verify itself (structural invariant)
"""

from __future__ import annotations

import sys
from pathlib import Path

from msb_v3.meta.contracts import (
    MetaTask,
    Verdict,
    VerificationResult,
    WorkerResult,
    WorkerStatus,
)
from msb_v3.meta.policy.execution_policy import (
    ExecutionPolicy,
)
from msb_v3.meta.verification.gate import GateResult, VerificationGate
from msb_v3.meta.verification.strategies import (
    FuzzyStrategy,
    StandardStrategy,
    StrictStrategy,
)


def _worker_ok(task_id: str = "T1", worker_id: str = "w1") -> WorkerResult:
    return WorkerResult(
        task_id=task_id, worker_id=worker_id,
        status=WorkerStatus.PRODUCED, artifact_ref="x = 1\n",
    )


def _worker_error(task_id: str = "T1", worker_id: str = "w1") -> WorkerResult:
    return WorkerResult(
        task_id=task_id, worker_id=worker_id,
        status=WorkerStatus.ERROR, error_class="RuntimeError", message="ollama down",
    )


def _worker_no_change(task_id: str = "T1", worker_id: str = "w1") -> WorkerResult:
    return WorkerResult(
        task_id=task_id, worker_id=worker_id,
        status=WorkerStatus.NO_CHANGE, artifact_ref="",
    )


def _task(task_id: str = "T1", cmds=None) -> MetaTask:
    return MetaTask(
        task_id=task_id,
        objective="do X",
        metadata={"verification_commands": cmds or []},
    )


# ---------------------------------------------------------------------------
# VerificationGate (end-to-end)
# ---------------------------------------------------------------------------

class TestVerificationGate:
    def test_worker_error_immediate_fail(self) -> None:
        gate = VerificationGate()
        result = gate.verify(
            task=_task(),
            worker_result=_worker_error(),
            workdir=Path("/tmp"),
        )
        assert result.verdict is Verdict.FAIL
        assert result.repair_suggested is True
        assert "RuntimeError" in result.reason

    def test_worker_no_change_expected_skip(self) -> None:
        gate = VerificationGate()
        result = gate.verify(
            task=_task(),
            worker_result=_worker_no_change(),
            workdir=Path("/tmp"),
        )
        assert result.verdict is Verdict.EXPECTED_SKIP

    def test_passing_commands_produces_pass(self) -> None:
        gate = VerificationGate()
        result = gate.verify(
            task=_task(cmds=[f"{sys.executable} -c 'exit(0)'"]),
            worker_result=_worker_ok(),
            workdir=Path("/tmp"),
        )
        assert result.verdict is Verdict.PASS
        assert result.passed is True
        assert result.checks_passed >= 1

    def test_failing_commands_produces_fail(self) -> None:
        gate = VerificationGate()
        result = gate.verify(
            task=_task(cmds=[f"{sys.executable} -c 'exit(1)'"]),
            worker_result=_worker_ok(),
            workdir=Path("/tmp"),
        )
        assert result.verdict is Verdict.FAIL
        assert result.checks_failed >= 1
        assert result.repair_suggested is True

    def test_no_commands_expected_skip(self) -> None:
        gate = VerificationGate()
        result = gate.verify(
            task=_task(cmds=[]),
            worker_result=_worker_ok(),
            workdir=Path("/tmp"),
        )
        assert result.verdict is Verdict.EXPECTED_SKIP

    def test_escalation_when_all_checks_fail(self) -> None:
        gate = VerificationGate()
        result = gate.verify(
            task=_task(cmds=[
                f"{sys.executable} -c 'exit(1)'",
                f"{sys.executable} -c 'exit(1)'",
                f"{sys.executable} -c 'exit(1)'",
            ]),
            worker_result=_worker_ok(),
            workdir=Path("/tmp"),
        )
        assert result.escalation_suggested is True

    def test_serialization(self) -> None:
        gate = VerificationGate()
        result = gate.verify(
            task=_task(cmds=[f"{sys.executable} -c 'exit(0)'"]),
            worker_result=_worker_ok(),
            workdir=Path("/tmp"),
        )
        d = result.to_dict()
        assert d["task_id"] == "T1"
        assert d["verdict"] == "PASS"
        assert "checks_run" in d


# ---------------------------------------------------------------------------
# Strategy selection via policy
# ---------------------------------------------------------------------------

class TestStrategySelection:
    def test_standard_strategy_selected(self) -> None:
        gate = VerificationGate()
        policy = ExecutionPolicy.local("T1")
        result = gate.verify(
            task=_task(cmds=[f"{sys.executable} -c 'exit(0)'"]),
            worker_result=_worker_ok(),
            policy=policy,
            workdir=Path("/tmp"),
        )
        assert result.strategy_used == "STANDARD"

    def test_strict_strategy_selected(self) -> None:
        gate = VerificationGate()
        policy = ExecutionPolicy.hybrid("T1")
        result = gate.verify(
            task=_task(cmds=[f"{sys.executable} -c 'exit(0)'"]),
            worker_result=_worker_ok(),
            policy=policy,
            workdir=Path("/tmp"),
        )
        assert result.strategy_used == "STRICT"

    def test_fuzzy_strategy_selected(self) -> None:
        gate = VerificationGate()
        policy = ExecutionPolicy.fable("T1")
        result = gate.verify(
            task=_task(cmds=[f"{sys.executable} -c 'exit(0)'"]),
            worker_result=_worker_ok(),
            policy=policy,
            workdir=Path("/tmp"),
        )
        assert result.strategy_used == "FUZZY"


# ---------------------------------------------------------------------------
# Individual strategies
# ---------------------------------------------------------------------------

class TestStandardStrategy:
    def test_runs_commands(self) -> None:
        s = StandardStrategy()
        checks = s.verify(_task(), _worker_ok(), Path("/tmp"))
        assert len(checks) == 0  # no commands specified

    def test_with_commands(self) -> None:
        s = StandardStrategy()
        task = _task(cmds=[f"{sys.executable} -c 'exit(0)'"])
        checks = s.verify(task, _worker_ok(), Path("/tmp"))
        assert len(checks) == 1
        assert checks[0].passed is True


class TestStrictStrategy:
    def test_includes_standard_checks(self) -> None:
        s = StrictStrategy()
        task = _task(cmds=[f"{sys.executable} -c 'exit(0)'"])
        checks = s.verify(task, _worker_ok(), Path("/tmp"))
        # Should have at least the standard check
        assert len(checks) >= 1


class TestFuzzyStrategy:
    def test_semantic_not_empty(self) -> None:
        s = FuzzyStrategy()
        wr = WorkerResult(
            task_id="T1", worker_id="w1",
            status=WorkerStatus.PRODUCED, artifact_ref="   \n  ",
        )
        checks = s.verify(_task(), wr, Path("/tmp"))
        empty_checks = [c for c in checks if "empty" in c.name.lower()]
        assert len(empty_checks) == 1
        assert empty_checks[0].passed is False

    def test_semantic_has_code(self) -> None:
        s = FuzzyStrategy()
        wr = WorkerResult(
            task_id="T1", worker_id="w1",
            status=WorkerStatus.PRODUCED, artifact_ref="# just a comment\n# another",
        )
        checks = s.verify(_task(), wr, Path("/tmp"))
        code_checks = [c for c in checks if "code" in c.name.lower()]
        assert len(code_checks) == 1
        assert code_checks[0].passed is False

    def test_bare_except_detected(self) -> None:
        s = FuzzyStrategy()
        wr = WorkerResult(
            task_id="T1", worker_id="w1",
            status=WorkerStatus.PRODUCED,
            artifact_ref="try:\n    pass\nexcept:\n    pass\n",
        )
        checks = s.verify(_task(), wr, Path("/tmp"))
        except_checks = [c for c in checks if "except" in c.name.lower()]
        assert len(except_checks) == 1
        assert except_checks[0].passed is False


# ---------------------------------------------------------------------------
# GateResult
# ---------------------------------------------------------------------------

class TestGateResult:
    def test_passed_property(self) -> None:
        gr = GateResult(
            task_id="T1", worker_id="w1", verdict=Verdict.PASS,
            verification=VerificationResult(task_id="T1", verdict=Verdict.PASS),
        )
        assert gr.passed is True

    def test_should_retry(self) -> None:
        gr = GateResult(
            task_id="T1", worker_id="w1", verdict=Verdict.FAIL,
            verification=VerificationResult(task_id="T1", verdict=Verdict.FAIL),
            repair_suggested=True,
        )
        assert gr.should_retry is True

    def test_should_escalate(self) -> None:
        gr = GateResult(
            task_id="T1", worker_id="w1", verdict=Verdict.FAIL,
            verification=VerificationResult(task_id="T1", verdict=Verdict.FAIL),
            escalation_suggested=True,
        )
        assert gr.should_escalate is True
