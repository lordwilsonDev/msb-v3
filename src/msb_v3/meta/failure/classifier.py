"""FailureClassifier — classifies failures into precise categories.

Blueprint §8:
    CODE FAILURE and INTEGRATION FAILURE must become different failure classes.
    Otherwise the system punishes the worker for bugs in the orchestration layer.

    Failure classes:
        MODEL_ERROR       — the model itself failed (crash, timeout, refusal)
        SPEC_ERROR        — the task specification was ambiguous or wrong
        CONTEXT_ERROR     — the worker received wrong/insufficient context
        TOOL_ERROR        — a tool the worker needed was unavailable or broken
        ENVIRONMENT_ERROR — the runtime environment was wrong
        TEST_ERROR        — the test harness itself has a bug
        INTEGRATION_ERROR — the task artifact didn't integrate with existing code
        DEPENDENCY_ERROR  — a prerequisite task failed or was missing
        VERIFICATION_ERROR — verification itself failed (not the worker's fault)
        HARNESS_ERROR     — the MSB orchestration layer has a bug
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from msb_v3.meta.contracts import FailureRecord, Verdict, VerificationResult

logger = logging.getLogger(__name__)


class FailureClass(str, Enum):
    """Precise failure classification (blueprint §8)."""

    MODEL_ERROR = "MODEL_ERROR"
    SPEC_ERROR = "SPEC_ERROR"
    CONTEXT_ERROR = "CONTEXT_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"
    TEST_ERROR = "TEST_ERROR"
    INTEGRATION_ERROR = "INTEGRATION_ERROR"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    VERIFICATION_ERROR = "VERIFICATION_ERROR"
    HARNESS_ERROR = "HARNESS_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass
class ClassificationResult:
    """The output of failure classification."""

    failure_class: FailureClass
    confidence: float  # 0.0–1.0
    evidence: List[str] = field(default_factory=list)
    likely_causes: List[str] = field(default_factory=list)
    recommended_action: str = ""
    repair_scope: List[str] = field(default_factory=list)
    retry_allowed: bool = True
    escalation_recommended: bool = False

    def to_failure_record(self, task_id: str, failure_id: str) -> FailureRecord:
        """Convert to a FailureRecord for the repair pipeline."""
        return FailureRecord(
            failure_id=failure_id,
            task_id=task_id,
            symptom=f"{self.failure_class.value}: {self.recommended_action}",
            evidence=self.evidence,
            likely_causes=self.likely_causes,
            recommended_action=self.recommended_action,
            repair_scope=self.repair_scope,
            retry_allowed=self.retry_allowed,
        )


class FailureClassifier:
    """Classifies failures from verification results.

    The classifier examines:
        1. The verification verdict and checks
        2. The worker result (error class, stderr)
        3. The task context (dependencies, constraints)

    It produces a ``ClassificationResult`` with the failure class,
    confidence, evidence, and recommended repair action.

    Usage::

        classifier = FailureClassifier()
        result = classifier.classify(verification_result, worker_result)
        if result.failure_class == FailureClass.HARNESS_ERROR:
            # Don't retry — fix the orchestration layer.
            ...
    """

    def classify(
        self,
        verification: VerificationResult,
        *,
        worker_error_class: str = "",
        worker_stderr: str = "",
        task_dependencies: Optional[List[str]] = None,
        dependency_results: Optional[Dict[str, Verdict]] = None,
    ) -> ClassificationResult:
        """Classify a failure from a verification result."""
        if verification.verdict is Verdict.PASS:
            return ClassificationResult(
                failure_class=FailureClass.UNKNOWN,
                confidence=1.0,
                evidence=["verification passed — no failure to classify"],
                recommended_action="no action needed",
            )

        # Check each failure class in priority order.
        checks = verification.checks
        failed_checks = [c for c in checks if not c.passed]

        # 1. Dependency failures.
        if dependency_results:
            failed_deps = [
                dep for dep, verdict in dependency_results.items()
                if verdict is Verdict.FAIL
            ]
            if failed_deps:
                return ClassificationResult(
                    failure_class=FailureClass.DEPENDENCY_ERROR,
                    confidence=0.95,
                    evidence=[f"failed dependency: {d}" for d in failed_deps],
                    likely_causes=[f"prerequisite task {d} failed" for d in failed_deps],
                    recommended_action="re-run or repair failed dependencies first",
                    retry_allowed=False,
                    escalation_recommended=True,
                )

        # 2. Harness/orchestration errors.
        if worker_error_class in ("HARNESS_ERROR", "ORCHESTRATION_ERROR"):
            return ClassificationResult(
                failure_class=FailureClass.HARNESS_ERROR,
                confidence=0.9,
                evidence=[f"worker reported: {worker_error_class}"],
                likely_causes=["orchestration layer bug", "context compilation error"],
                recommended_action="inspect harness and context compiler",
                retry_allowed=False,
                escalation_recommended=True,
            )

        # 3. Test harness errors (tests themselves broken).
        test_failures = [c for c in failed_checks if "test" in c.name.lower()]
        if test_failures and not any("ruff" in c.name or "mypy" in c.name for c in failed_checks):
            # Only test failures — could be harness bug.
            if any("error" in (c.detail or "").lower() for c in test_failures):
                return ClassificationResult(
                    failure_class=FailureClass.TEST_ERROR,
                    confidence=0.6,
                    evidence=[f"{c.name}: {c.detail}" for c in test_failures],
                    likely_causes=["test harness bug", "test fixture missing"],
                    recommended_action="inspect test infrastructure",
                    retry_allowed=True,
                )

        # 4. Model errors (crash, timeout, refusal).
        if worker_error_class in ("TIMEOUT", "CRASH", "REFUSAL", "CONTEXT_LENGTH"):
            return ClassificationResult(
                failure_class=FailureClass.MODEL_ERROR,
                confidence=0.85,
                evidence=[f"worker error: {worker_error_class}"],
                likely_causes=["model could not handle task within constraints"],
                recommended_action="try larger model or simplify task",
                retry_allowed=True,
            )

        # 5. Spec errors (ambiguous or wrong task specification).
        if not verification.checks and verification.message:
            return ClassificationResult(
                failure_class=FailureClass.SPEC_ERROR,
                confidence=0.5,
                evidence=[f"no checks ran: {verification.message}"],
                likely_causes=["task specification ambiguous", "acceptance criteria unclear"],
                recommended_action="revise task specification",
                retry_allowed=True,
            )

        # 6. Integration errors (artifact didn't integrate).
        integration_failures = [
            c for c in failed_checks
            if any(kw in c.name.lower() for kw in ["import", "integration", "compile", "mypy"])
        ]
        if integration_failures:
            return ClassificationResult(
                failure_class=FailureClass.INTEGRATION_ERROR,
                confidence=0.75,
                evidence=[f"{c.name}: {c.detail}" for c in integration_failures],
                likely_causes=["artifact incompatible with existing code", "missing imports"],
                recommended_action="inspect integration boundaries",
                retry_allowed=True,
            )

        # 7. Context errors (wrong or insufficient context).
        if any("context" in (c.detail or "").lower() for c in failed_checks):
            return ClassificationResult(
                failure_class=FailureClass.CONTEXT_ERROR,
                confidence=0.6,
                evidence=[f"{c.name}: {c.detail}" for c in failed_checks if "context" in (c.detail or "").lower()],
                likely_causes=["context compiler selected wrong files", "missing relevant code"],
                recommended_action="review context compilation for this task",
                retry_allowed=True,
            )

        # 8. Tool errors.
        if worker_error_class in ("TOOL_UNAVAILABLE", "TOOL_ERROR"):
            return ClassificationResult(
                failure_class=FailureClass.TOOL_ERROR,
                confidence=0.8,
                evidence=[f"tool error: {worker_error_class}"],
                likely_causes=["required tool unavailable or broken"],
                recommended_action="check tool availability and configuration",
                retry_allowed=True,
            )

        # 9. Environment errors.
        if worker_error_class in ("ENVIRONMENT_ERROR", "MISSING_DEPENDENCY"):
            return ClassificationResult(
                failure_class=FailureClass.ENVIRONMENT_ERROR,
                confidence=0.8,
                evidence=[f"environment error: {worker_error_class}"],
                likely_causes=["runtime environment mismatch"],
                recommended_action="check environment configuration",
                retry_allowed=True,
            )

        # 10. Default: model error (most common for worker failures).
        return ClassificationResult(
            failure_class=FailureClass.MODEL_ERROR,
            confidence=0.4,
            evidence=[f"checks failed: {[c.name for c in failed_checks]}"],
            likely_causes=["model produced incorrect output", "task too complex for worker"],
            recommended_action="retry with adjusted context or escalate",
            retry_allowed=True,
        )
