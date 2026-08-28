"""VerificationGate — the independent verifier that makes the system falsifiable.

The VerificationGate receives:
    - a MetaTask (what was supposed to happen)
    - a WorkerResult (what the worker claims happened)
    - an ExecutionPolicy (how strictly to verify)

It produces:
    - a GateResult containing VerificationResult + evidence + decision

Critical invariant:
    The worker is NEVER allowed to verify itself.
    The gate is always a separate component.

Architecture:
    WorkerResult + MetaTask
            ↓
    Strategy Selection (from ExecutionPolicy.verification)
            ↓
    Check Execution (deterministic)
            ↓
    Evidence Capture
            ↓
    Verdict Decision
            ↓
    GateResult
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.meta.contracts import (
    MetaTask,
    Verdict,
    VerificationResult,
    WorkerResult,
    WorkerStatus,
)
from msb_v3.meta.policy.execution_policy import ExecutionPolicy, VerificationStrategy
from msb_v3.meta.verification.strategies import (
    FuzzyStrategy,
    StandardStrategy,
    StrictStrategy,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class GateResult:
    """The output of verification — the system's independent judgment."""

    task_id: str
    worker_id: str
    verdict: Verdict
    verification: VerificationResult

    # Evidence chain.
    checks_run: int = 0
    checks_passed: int = 0
    checks_failed: int = 0
    strategy_used: str = ""

    # Decision metadata.
    decision_at: str = field(default_factory=_now)
    confidence: float = 0.0
    reason: str = ""
    evidence_refs: List[str] = field(default_factory=list)

    # For repair pipeline.
    repair_suggested: bool = False
    escalation_suggested: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for evidence/audit trail."""
        return {
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "verdict": self.verdict.value,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "strategy_used": self.strategy_used,
            "confidence": self.confidence,
            "reason": self.reason,
            "repair_suggested": self.repair_suggested,
            "escalation_suggested": self.escalation_suggested,
            "decision_at": self.decision_at,
        }

    @property
    def passed(self) -> bool:
        return self.verdict is Verdict.PASS

    @property
    def should_retry(self) -> bool:
        return self.verdict is Verdict.FAIL and self.repair_suggested

    @property
    def should_escalate(self) -> bool:
        return self.escalation_suggested


class VerificationGate:
    """The independent verifier.

    Usage::

        gate = VerificationGate()
        result = gate.verify(
            task=meta_task,
            worker_result=worker_result,
            policy=execution_policy,
            workdir=Path("/tmp/work"),
        )
        if result.passed:
            # Worker output is verified
            ...
        elif result.should_retry:
            # Feed failure to repair pipeline
            ...
        elif result.should_escalate:
            # Worker can't handle this — find a larger one
            ...
    """

    def __init__(self) -> None:
        self._strategies: Dict[VerificationStrategy, Any] = {
            VerificationStrategy.STANDARD: StandardStrategy(),
            VerificationStrategy.STRICT: StrictStrategy(),
            VerificationStrategy.FUZZY: FuzzyStrategy(),
        }

    def verify(
        self,
        *,
        task: MetaTask,
        worker_result: WorkerResult,
        policy: Optional[ExecutionPolicy] = None,
        workdir: Path,
        verification_commands: Optional[List[str]] = None,
    ) -> GateResult:
        """Run verification and return the gate's independent judgment."""
        # Select strategy from policy.
        strategy_key = VerificationStrategy.STANDARD
        if policy is not None:
            strategy_key = policy.shape.verification

        strategy = self._strategies.get(strategy_key, StandardStrategy())

        # Worker error → immediate fail.
        if worker_result.status is WorkerStatus.ERROR:
            return GateResult(
                task_id=task.task_id,
                worker_id=worker_result.worker_id,
                verdict=Verdict.FAIL,
                verification=VerificationResult(
                    task_id=task.task_id,
                    verdict=Verdict.FAIL,
                    worker_id=worker_result.worker_id,
                    message=f"worker error: {worker_result.error_class}: {worker_result.message}",
                ),
                strategy_used=strategy_key.value,
                reason=f"worker error: {worker_result.error_class}",
                repair_suggested=True,
            )

        # Worker produced nothing → expected skip or fail.
        if worker_result.status is WorkerStatus.NO_CHANGE:
            return GateResult(
                task_id=task.task_id,
                worker_id=worker_result.worker_id,
                verdict=Verdict.EXPECTED_SKIP,
                verification=VerificationResult(
                    task_id=task.task_id,
                    verdict=Verdict.EXPECTED_SKIP,
                    worker_id=worker_result.worker_id,
                    message="worker produced no artifact",
                ),
                strategy_used=strategy_key.value,
                reason="no artifact produced",
            )

        # Run verification checks.
        checks = strategy.verify(task, worker_result, workdir, verification_commands=verification_commands)

        # Build VerificationResult.
        passed_count = sum(1 for c in checks if c.passed)
        failed_count = sum(1 for c in checks if not c.passed)
        verdict = Verdict.PASS if failed_count == 0 and passed_count > 0 else Verdict.FAIL

        # No checks run → expected skip.
        if len(checks) == 0:
            verdict = Verdict.EXPECTED_SKIP

        vr = VerificationResult(
            task_id=task.task_id,
            verdict=verdict,
            checks=checks,
            worker_id=worker_result.worker_id,
        )

        # Build gate result.
        confidence = passed_count / max(1, len(checks))

        # Determine repair/escalation suggestions.
        repair_suggested = verdict is Verdict.FAIL
        escalation_suggested = False

        # If all checks failed and we have many checks, escalate.
        if failed_count == len(checks) and len(checks) >= 3:
            escalation_suggested = True

        reason_parts = []
        if failed_count > 0:
            failed_names = [c.name for c in checks if not c.passed]
            reason_parts.append(f"failed: {', '.join(failed_names)}")
        if passed_count > 0:
            reason_parts.append(f"passed: {passed_count}/{len(checks)}")

        return GateResult(
            task_id=task.task_id,
            worker_id=worker_result.worker_id,
            verdict=verdict,
            verification=vr,
            checks_run=len(checks),
            checks_passed=passed_count,
            checks_failed=failed_count,
            strategy_used=strategy_key.value,
            confidence=confidence,
            reason="; ".join(reason_parts) if reason_parts else "no checks",
            repair_suggested=repair_suggested,
            escalation_suggested=escalation_suggested,
        )
