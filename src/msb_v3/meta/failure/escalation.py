"""EscalationPolicy — determines when and how to escalate to larger workers.

Blueprint §17:
    Multi-worker escalation:
        Qwen 3B → failure threshold → larger worker

    Example:
        Qwen 3B
           ↓
        Qwen 8B
           ↓
        DeepSeek
           ↓
        Claude
           ↓
        Gemini
           ↓
        human/operator

    The escalation decision is policy-controlled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from msb_v3.meta.contracts import Complexity, MetaTask
from msb_v3.meta.failure.classifier import FailureClass
from msb_v3.meta.routing.worker_registry import RegisteredWorker, WorkerRegistry

logger = logging.getLogger(__name__)


@dataclass
class EscalationDecision:
    """The output of escalation evaluation."""

    should_escalate: bool = False
    current_worker_id: str = ""
    target_worker_id: Optional[str] = None
    reason: str = ""
    attempts_on_current: int = 0
    max_attempts_before_escalation: int = 3
    failure_classes_seen: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "should_escalate": self.should_escalate,
            "current_worker_id": self.current_worker_id,
            "target_worker_id": self.target_worker_id,
            "reason": self.reason,
            "attempts_on_current": self.attempts_on_current,
        }


class EscalationPolicy:
    """Determines when and how to escalate to a larger worker.

    Escalation triggers:
        1. Worker failed N times on the same task (N = max_attempts)
        2. Failure class is MODEL_ERROR (worker can't handle complexity)
        3. Failure class is CONTEXT_ERROR (worker's context capacity exceeded)
        4. Task complexity exceeds worker's rated capacity

    Escalation does NOT happen for:
        - HARNESS_ERROR (fix the harness, don't escalate)
        - DEPENDENCY_ERROR (fix dependencies, don't escalate)
        - ENVIRONMENT_ERROR (fix environment, don't escalate)

    Usage::

        policy = EscalationPolicy(worker_registry=registry)
        decision = policy.evaluate(task, current_worker, attempts=3)
        if decision.should_escalate:
            next_worker = registry.get(decision.target_worker_id)
    """

    def __init__(
        self,
        *,
        worker_registry: WorkerRegistry,
        max_attempts: int = 3,
    ) -> None:
        self._workers = worker_registry
        self._max_attempts = max_attempts

    def evaluate(
        self,
        task: MetaTask,
        current_worker: RegisteredWorker,
        *,
        attempts: int = 1,
        failure_class: Optional[FailureClass] = None,
        failure_classes_seen: Optional[List[FailureClass]] = None,
    ) -> EscalationDecision:
        """Evaluate whether to escalate from the current worker."""
        decision = EscalationDecision(
            current_worker_id=current_worker.worker_id,
            attempts_on_current=attempts,
            max_attempts_before_escalation=self._max_attempts,
            failure_classes_seen=[fc.value for fc in (failure_classes_seen or [])],
        )

        # 1. Harness/dependency/environment errors — don't escalate, fix root cause.
        if failure_class in (
            FailureClass.HARNESS_ERROR,
            FailureClass.DEPENDENCY_ERROR,
            FailureClass.ENVIRONMENT_ERROR,
            FailureClass.TEST_ERROR,
        ):
            decision.should_escalate = False
            decision.reason = f"{failure_class.value} — fix root cause, not worker"
            return decision

        # 2. Spec errors — don't escalate, fix the spec.
        if failure_class is FailureClass.SPEC_ERROR:
            decision.should_escalate = False
            decision.reason = "spec error — revise task specification"
            return decision

        # 3. Exceeded max attempts — escalate.
        if attempts >= self._max_attempts:
            target = self._workers.escalate(current_worker.worker_id)
            if target:
                decision.should_escalate = True
                decision.target_worker_id = target.worker_id
                decision.reason = f"exceeded {attempts} attempts — escalate to {target.worker_id}"
            else:
                decision.should_escalate = False
                decision.reason = "exceeded max attempts but no larger worker available"
            return decision

        # 4. Model error on first attempt — might be transient, don't escalate yet.
        if failure_class is FailureClass.MODEL_ERROR and attempts < 2:
            decision.should_escalate = False
            decision.reason = "model error on early attempt — retry before escalating"
            return decision

        # 5. Repeated model errors — escalate.
        if failure_class is FailureClass.MODEL_ERROR and attempts >= 2:
            target = self._workers.escalate(current_worker.worker_id)
            if target:
                decision.should_escalate = True
                decision.target_worker_id = target.worker_id
                decision.reason = f"repeated model errors — escalate to {target.worker_id}"
            return decision

        # 6. Context error with high complexity — worker may lack capacity.
        if failure_class is FailureClass.CONTEXT_ERROR:
            if task.complexity in (Complexity.HIGH, Complexity.CRITICAL):
                target = self._workers.escalate(current_worker.worker_id)
                if target:
                    decision.should_escalate = True
                    decision.target_worker_id = target.worker_id
                    decision.reason = f"context error on {task.complexity.value} task — escalate"
                    return decision

        # 7. Default: don't escalate.
        decision.should_escalate = False
        decision.reason = "no escalation trigger met"
        return decision
