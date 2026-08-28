"""META-1D: Failure compiler tests — classifier, repair policy, escalation.

Tests verify:
  - FailureClassifier classifies all failure classes
  - ClassificationResult produces valid FailureRecord
  - RepairPolicy generates appropriate repair plans
  - RepairPolicy escalation after max attempts
  - EscalationPolicy escalation triggers and suppressions
  - No escalation for harness/dependency/environment errors
  - Escalation to next worker when threshold exceeded
"""

from __future__ import annotations

from msb_v3.meta.contracts import (
    CheckResult,
    MetaTask,
    Verdict,
    VerificationResult,
)
from msb_v3.meta.failure.classifier import (
    ClassificationResult,
    FailureClass,
    FailureClassifier,
)
from msb_v3.meta.failure.escalation import EscalationPolicy
from msb_v3.meta.failure.repair import RepairPolicy
from msb_v3.meta.routing.worker_registry import RegisteredWorker, WorkerRegistry


def _fail_verification(checks=None, message="") -> VerificationResult:
    return VerificationResult(
        task_id="T1",
        verdict=Verdict.FAIL,
        checks=checks or [],
        message=message,
    )


def _pass_verification() -> VerificationResult:
    return VerificationResult(task_id="T1", verdict=Verdict.PASS)


# ---------------------------------------------------------------------------
# FailureClassifier
# ---------------------------------------------------------------------------

class TestFailureClassifier:
    def test_passing_verification_returns_no_failure(self) -> None:
        clf = FailureClassifier()
        result = clf.classify(_pass_verification())
        assert result.confidence == 1.0
        assert "no failure" in result.evidence[0].lower()

    def test_dependency_error_when_dep_fails(self) -> None:
        clf = FailureClassifier()
        result = clf.classify(
            _fail_verification(),
            dependency_results={"dep-1": Verdict.FAIL},
        )
        assert result.failure_class is FailureClass.DEPENDENCY_ERROR
        assert result.retry_allowed is False

    def test_harness_error_when_reported(self) -> None:
        clf = FailureClassifier()
        result = clf.classify(
            _fail_verification(),
            worker_error_class="HARNESS_ERROR",
        )
        assert result.failure_class is FailureClass.HARNESS_ERROR
        assert result.retry_allowed is False

    def test_model_error_on_timeout(self) -> None:
        clf = FailureClassifier()
        result = clf.classify(
            _fail_verification(checks=[CheckResult(name="pytest", passed=False, detail="timeout")]),
            worker_error_class="TIMEOUT",
        )
        assert result.failure_class is FailureClass.MODEL_ERROR
        assert result.retry_allowed is True

    def test_integration_error_on_mypy_failure(self) -> None:
        clf = FailureClassifier()
        result = clf.classify(
            _fail_verification(checks=[
                CheckResult(name="mypy", passed=False, detail="name 'x' is not defined"),
            ]),
        )
        assert result.failure_class is FailureClass.INTEGRATION_ERROR

    def test_spec_error_when_no_checks_and_message(self) -> None:
        clf = FailureClassifier()
        result = clf.classify(
            VerificationResult(task_id="T1", verdict=Verdict.FAIL, message="ambiguous"),
        )
        assert result.failure_class is FailureClass.SPEC_ERROR

    def test_to_failure_record(self) -> None:
        cr = ClassificationResult(
            failure_class=FailureClass.MODEL_ERROR,
            confidence=0.8,
            evidence=["timeout"],
            recommended_action="try larger model",
        )
        fr = cr.to_failure_record("T1", "FAIL-001")
        assert fr.task_id == "T1"
        assert fr.failure_id == "FAIL-001"
        assert "MODEL_ERROR" in fr.symptom


# ---------------------------------------------------------------------------
# RepairPolicy
# ---------------------------------------------------------------------------

class TestRepairPolicy:
    def test_model_error_first_attempt_retries_same(self) -> None:
        policy = RepairPolicy()
        task = MetaTask(task_id="T1", objective="X")
        cls = ClassificationResult(failure_class=FailureClass.MODEL_ERROR, confidence=0.8)
        plan = policy.plan_repair(task, cls, attempt=1)
        assert plan.repair_action == "retry_same"
        assert plan.should_retry is True

    def test_model_error_second_attempt_simplifies(self) -> None:
        policy = RepairPolicy()
        task = MetaTask(task_id="T1", objective="X")
        cls = ClassificationResult(failure_class=FailureClass.MODEL_ERROR, confidence=0.8)
        plan = policy.plan_repair(task, cls, attempt=2)
        assert plan.repair_action == "retry_simplified"
        assert plan.simplify_objective is True

    def test_model_error_third_attempt_escalates(self) -> None:
        policy = RepairPolicy()
        task = MetaTask(task_id="T1", objective="X")
        cls = ClassificationResult(failure_class=FailureClass.MODEL_ERROR, confidence=0.8)
        plan = policy.plan_repair(task, cls, attempt=3)
        assert plan.repair_action == "escalate"

    def test_context_error_adjusts_context(self) -> None:
        policy = RepairPolicy()
        task = MetaTask(task_id="T1", objective="X")
        cls = ClassificationResult(failure_class=FailureClass.CONTEXT_ERROR, confidence=0.7)
        plan = policy.plan_repair(task, cls)
        assert plan.repair_action == "retry_adjusted_context"
        assert "context" in plan.variables_to_change

    def test_harness_error_does_not_retry(self) -> None:
        policy = RepairPolicy()
        task = MetaTask(task_id="T1", objective="X")
        cls = ClassificationResult(failure_class=FailureClass.HARNESS_ERROR, confidence=0.9)
        plan = policy.plan_repair(task, cls)
        assert plan.repair_action == "fix_harness"
        assert plan.should_retry is False

    def test_dependency_error_escalates(self) -> None:
        policy = RepairPolicy()
        task = MetaTask(task_id="T1", objective="X")
        cls = ClassificationResult(
            failure_class=FailureClass.DEPENDENCY_ERROR,
            confidence=0.95,
            retry_allowed=False,
        )
        plan = policy.plan_repair(task, cls)
        assert plan.repair_action == "escalate"

    def test_spec_error_fixes_spec(self) -> None:
        policy = RepairPolicy()
        task = MetaTask(task_id="T1", objective="X")
        cls = ClassificationResult(failure_class=FailureClass.SPEC_ERROR, confidence=0.6)
        plan = policy.plan_repair(task, cls)
        assert plan.repair_action == "fix_spec"


# ---------------------------------------------------------------------------
# EscalationPolicy
# ---------------------------------------------------------------------------

class TestEscalationPolicy:
    def _build_registry(self) -> WorkerRegistry:
        reg = WorkerRegistry()
        reg.register(RegisteredWorker(
            worker_id="qwen3b", model_id="qwen3-3b",
            capabilities=["python"], max_context_tokens=8192,
        ))
        reg.register(RegisteredWorker(
            worker_id="qwen8b", model_id="qwen3-8b",
            capabilities=["python", "research"], max_context_tokens=16384,
        ))
        reg.register(RegisteredWorker(
            worker_id="deepseek", model_id="deepseek-v3",
            capabilities=["python", "research", "cloud"], max_context_tokens=32768,
        ))
        return reg

    def test_no_escalation_for_harness_error(self) -> None:
        reg = self._build_registry()
        policy = EscalationPolicy(worker_registry=reg, max_attempts=3)
        worker = reg.get("qwen3b")
        task = MetaTask(task_id="T1", objective="X")
        decision = policy.evaluate(task, worker, attempts=3, failure_class=FailureClass.HARNESS_ERROR)
        assert decision.should_escalate is False

    def test_no_escalation_for_dependency_error(self) -> None:
        reg = self._build_registry()
        policy = EscalationPolicy(worker_registry=reg, max_attempts=3)
        worker = reg.get("qwen3b")
        task = MetaTask(task_id="T1", objective="X")
        decision = policy.evaluate(task, worker, attempts=3, failure_class=FailureClass.DEPENDENCY_ERROR)
        assert decision.should_escalate is False

    def test_no_escalation_for_spec_error(self) -> None:
        reg = self._build_registry()
        policy = EscalationPolicy(worker_registry=reg, max_attempts=3)
        worker = reg.get("qwen3b")
        task = MetaTask(task_id="T1", objective="X")
        decision = policy.evaluate(task, worker, attempts=1, failure_class=FailureClass.SPEC_ERROR)
        assert decision.should_escalate is False

    def test_escalation_after_max_attempts(self) -> None:
        reg = self._build_registry()
        policy = EscalationPolicy(worker_registry=reg, max_attempts=3)
        worker = reg.get("qwen3b")
        task = MetaTask(task_id="T1", objective="X")
        decision = policy.evaluate(task, worker, attempts=3, failure_class=FailureClass.MODEL_ERROR)
        assert decision.should_escalate is True
        assert decision.target_worker_id == "qwen8b"

    def test_no_escalation_below_max_attempts(self) -> None:
        reg = self._build_registry()
        policy = EscalationPolicy(worker_registry=reg, max_attempts=3)
        worker = reg.get("qwen3b")
        task = MetaTask(task_id="T1", objective="X")
        decision = policy.evaluate(task, worker, attempts=1, failure_class=FailureClass.MODEL_ERROR)
        assert decision.should_escalate is False

    def test_escalation_chain_deepseek_after_8b(self) -> None:
        reg = self._build_registry()
        policy = EscalationPolicy(worker_registry=reg, max_attempts=2)
        worker = reg.get("qwen8b")
        task = MetaTask(task_id="T1", objective="X")
        decision = policy.evaluate(task, worker, attempts=2, failure_class=FailureClass.MODEL_ERROR)
        assert decision.should_escalate is True
        assert decision.target_worker_id == "deepseek"

    def test_no_escalation_when_no_larger_worker(self) -> None:
        reg = WorkerRegistry()
        reg.register(RegisteredWorker(worker_id="solo", model_id="solo-model", capabilities=["x"]))
        policy = EscalationPolicy(worker_registry=reg, max_attempts=1)
        worker = reg.get("solo")
        task = MetaTask(task_id="T1", objective="X")
        decision = policy.evaluate(task, worker, attempts=1, failure_class=FailureClass.MODEL_ERROR)
        # No larger worker → no escalation despite exceeding threshold
        assert decision.should_escalate is False
