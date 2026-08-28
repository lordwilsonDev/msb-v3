"""RepairPolicy — determines repair strategy from failure classification.

Blueprint §13, §15:
    Failure → Diagnosis → Change ONE variable → Recompile → Retry → Verify

    Don't blindly retry the same prompt.

The RepairPolicy takes a ``ClassificationResult`` and produces a repair plan:
    - what variable to change
    - how to change it
    - whether to retry or escalate
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from msb_v3.meta.contracts import MetaTask
from msb_v3.meta.failure.classifier import ClassificationResult, FailureClass

logger = logging.getLogger(__name__)


@dataclass
class RepairPlan:
    """A structured repair plan for a failed task."""

    task_id: str
    failure_class: FailureClass
    repair_action: str  # "retry_same" | "retry_adjusted_context" | "retry_simplified" | "escalate" | "fix_harness" | "fix_spec"
    variables_to_change: List[str] = field(default_factory=list)  # e.g. ["context", "model"]
    new_context: Dict[str, Any] = field(default_factory=dict)
    new_model: Optional[str] = None
    simplify_objective: bool = False
    max_retries: int = 2
    current_retry: int = 0
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def should_retry(self) -> bool:
        return self.repair_action.startswith("retry") and self.current_retry < self.max_retries

    @property
    def should_escalate(self) -> bool:
        return self.repair_action == "escalate" or self.current_retry >= self.max_retries


class RepairPolicy:
    """Determines repair strategy from failure classification.

    The policy follows the blueprint principle:
        Change ONE variable at a time.

    Variables that can change:
        - context (more/less/different files)
        - model (larger/smaller)
        - objective (simplified/reworded)
        - tools (different toolset)
        - constraints (relaxed/tightened)

    Usage::

        policy = RepairPolicy()
        plan = policy.plan_repair(task, classification, attempt=2)
        if plan.should_escalate:
            # Find a larger worker.
            ...
    """

    def plan_repair(
        self,
        task: MetaTask,
        classification: ClassificationResult,
        *,
        attempt: int = 1,
        previous_plans: Optional[List[RepairPlan]] = None,
    ) -> RepairPlan:
        """Generate a repair plan based on the failure classification."""
        plan = RepairPlan(
            task_id=task.task_id,
            failure_class=classification.failure_class,
            repair_action="retry_same",
            current_retry=attempt - 1,
        )

        # If the classification says no retry, escalate immediately.
        if not classification.retry_allowed or classification.escalation_recommended:
            plan.repair_action = "escalate"
            plan.reason = f"{classification.failure_class.value} — escalation recommended"
            return plan

        # Strategy depends on failure class.
        if classification.failure_class is FailureClass.MODEL_ERROR:
            plan = self._repair_model_error(task, classification, plan, attempt, previous_plans)

        elif classification.failure_class is FailureClass.CONTEXT_ERROR:
            plan.repair_action = "retry_adjusted_context"
            plan.variables_to_change = ["context"]
            plan.reason = "context was insufficient or wrong — adjust context compilation"

        elif classification.failure_class is FailureClass.SPEC_ERROR:
            plan.repair_action = "fix_spec"
            plan.variables_to_change = ["objective", "constraints"]
            plan.reason = "task specification was ambiguous — revise and re-translate"

        elif classification.failure_class is FailureClass.INTEGRATION_ERROR:
            plan.repair_action = "retry_adjusted_context"
            plan.variables_to_change = ["context", "constraints"]
            plan.reason = "integration failed — add more integration context"

        elif classification.failure_class is FailureClass.HARNESS_ERROR:
            plan.repair_action = "fix_harness"
            plan.reason = "orchestration layer bug — do not retry, fix harness"

        elif classification.failure_class is FailureClass.TEST_ERROR:
            plan.repair_action = "fix_harness"
            plan.reason = "test infrastructure bug — fix tests before retrying"

        elif classification.failure_class is FailureClass.DEPENDENCY_ERROR:
            plan.repair_action = "escalate"
            plan.reason = "prerequisite failed — resolve dependencies first"

        elif classification.failure_class is FailureClass.TOOL_ERROR:
            plan.repair_action = "retry_adjusted_context"
            plan.variables_to_change = ["tools"]
            plan.reason = "tool unavailable — adjust tool policy"

        elif classification.failure_class is FailureClass.ENVIRONMENT_ERROR:
            plan.repair_action = "escalate"
            plan.reason = "environment misconfigured — fix environment before retry"

        else:
            plan.repair_action = "retry_same"
            plan.reason = "unknown failure — retry with same parameters"

        return plan

    def _repair_model_error(
        self,
        task: MetaTask,
        classification: ClassificationResult,
        plan: RepairPlan,
        attempt: int,
        previous_plans: Optional[List[RepairPlan]],
    ) -> RepairPlan:
        """Determine repair strategy for model errors.

        Strategy:
            attempt 1: retry with same model (transient failure)
            attempt 2: simplify the objective
            attempt 3+: escalate to larger model
        """
        if attempt <= 1:
            plan.repair_action = "retry_same"
            plan.reason = "transient model error — retry"
        elif attempt == 2:
            plan.repair_action = "retry_simplified"
            plan.simplify_objective = True
            plan.variables_to_change = ["objective"]
            plan.reason = "repeated model error — simplify task"
        else:
            plan.repair_action = "escalate"
            plan.reason = f"model failed {attempt} times — escalate to larger worker"

        return plan
