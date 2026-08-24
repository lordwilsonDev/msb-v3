"""Work Plan — convert a PLEI NextAction into a governed execution plan.

Takes a single ScoredAction from the Phase 5 decision engine and produces
a concrete WorkPlan with:
    1. Goal statement (what dsh receives as its task)
    2. Provider routing (which provider executes each step)
    3. Risk tiers per step (for ActionGate gating)
    4. Verification gates (MoIE claims to verify after execution)
    5. Prerequisites check (ensure all dependencies are met)
    6. Fallback chain (if primary provider fails)

The WorkPlan is the bridge between PLEI's intelligence layer and MSB's
governed execution loop. Every step is annotated with a risk tier so
the ActionGate can enforce the sovereignty boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WorkPlanStep:
    """One step in a governed work plan."""

    step_id: str
    sequence: int  # 1-based ordering
    description: str
    goal: str  # what the provider receives

    # Governance
    risk_tier: int = 2  # 1–4
    capabilities_required: tuple[str, ...] = ()  # needed by provider
    tainted_inputs: bool = False  # does this step consume untrusted data?

    # Routing
    preferred_provider_id: str = ""
    fallback_provider_ids: list[str] = field(default_factory=list)

    # Verification
    verification_claims: list[str] = field(
        default_factory=list
    )  # MoIE claims to verify

    # Estimated
    estimated_duration_s: float = 60.0
    reversible: bool = True


@dataclass(slots=True)
class WorkPlan:
    """A complete governed work plan from one PLEI decision."""

    plan_id: str
    source_action_id: str  # the NextAction.action_id this derives from
    source_action_description: str
    category: str  # gap_close | risk_mitigate | debt_reduce

    steps: list[WorkPlanStep] = field(default_factory=list)

    # Governance
    max_risk_tier: int = 4
    requires_operator_approval: bool = False
    approved_capabilities: set[str] = field(default_factory=set)

    # Metadata
    expected_outcome: str = ""
    evidence_chain: list[str] = field(default_factory=list)

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def primary_providers(self) -> list[str]:
        """Unique provider IDs used in this plan."""
        seen: set[str] = set()
        results: list[str] = []
        for s in self.steps:
            if s.preferred_provider_id and s.preferred_provider_id not in seen:
                seen.add(s.preferred_provider_id)
                results.append(s.preferred_provider_id)
        return results


def build_work_plan(
    next_action: dict[str, Any],
    provider_sel: dict[str, Any] | None = None,
) -> WorkPlan:
    """Convert a NextAction dict into a governed WorkPlan.

    The NextAction comes from the Phase 5 decision engine.
    Provider selection data (from provider_selection.py) enriches routing.
    """
    action_id = next_action.get("action_id", "unknown")
    description = next_action.get("description", "unknown action")
    category = next_action.get("category", "gap_close")
    score = next_action.get("score", 0.0)

    # Determine risk tier from category
    risk_tier_map = {
        "gap_close": 2,  # installing a skill is low-risk
        "risk_mitigate": 3,  # mitigating risk may touch production
        "debt_reduce": 3,  # refactoring needs care
        "capability_add": 2,
    }
    risk_tier = risk_tier_map.get(category, 3)

    # Build steps
    steps = _build_steps_for_category(
        category=category,
        description=description,
        action_id=action_id,
        score=score,
        base_risk_tier=risk_tier,
        provider_sel=provider_sel,
    )

    plan = WorkPlan(
        plan_id=f"plan:{action_id}",
        source_action_id=action_id,
        source_action_description=description,
        category=category,
        steps=steps,
        max_risk_tier=risk_tier,
        requires_operator_approval=risk_tier >= 4,
        expected_outcome=next_action.get("expected_outcome", ""),
        evidence_chain=next_action.get("validation_checks", []),
    )

    return plan


def _build_steps_for_category(
    category: str,
    description: str,
    action_id: str,
    score: float,
    base_risk_tier: int,
    provider_sel: dict[str, Any] | None,
) -> list[WorkPlanStep]:
    """Build the step sequence for a work plan category."""
    steps: list[WorkPlanStep] = []
    primary_prov = ""
    fallback_provs: list[str] = []

    if provider_sel:
        sel_primary = provider_sel.get("primary")
        if sel_primary and isinstance(sel_primary, dict):
            primary_prov = sel_primary.get("provider_id", "")
        for fb in provider_sel.get("fallbacks", []):
            if isinstance(fb, dict):
                fallback_provs.append(fb.get("provider_id", ""))

    if category == "gap_close":
        # Step 1: Verify the gap still exists
        steps.append(WorkPlanStep(
            step_id=f"{action_id}.1",
            sequence=1,
            description="Verify the capability gap still exists",
            goal=f"Check whether capability gap described as '{description}' is still present",
            risk_tier=1,
            capabilities_required=(),
            preferred_provider_id=primary_prov or "local.slice",
            verification_claims=[f"Gap '{action_id}' confirmed present"],
            estimated_duration_s=15.0,
            reversible=True,
        ))

        # Step 2: Install/activate the skill
        steps.append(WorkPlanStep(
            step_id=f"{action_id}.2",
            sequence=2,
            description=f"Execute: {description}",
            goal=f"Install or activate the capability: {description}. "
                 f"This is a governed action — verify the skill exists, "
                 f"install it if needed, and report the outcome.",
            risk_tier=base_risk_tier,
            capabilities_required=("search_query",),
            preferred_provider_id=primary_prov or "cli.codebuddy",
            fallback_provider_ids=fallback_provs,
            verification_claims=[
                f"Capability '{action_id}' is now available",
                "No errors during installation",
            ],
            estimated_duration_s=120.0,
            reversible=True,
        ))

        # Step 3: Verify the gap is closed
        steps.append(WorkPlanStep(
            step_id=f"{action_id}.3",
            sequence=3,
            description="Verify the gap is now closed",
            goal=f"Confirm that the capability gap '{description}' is resolved. "
                 f"Check that the skill is installed and a provider is available.",
            risk_tier=1,
            capabilities_required=(),
            preferred_provider_id=primary_prov or "local.slice",
            verification_claims=[f"Gap '{action_id}' is CLOSED"],
            estimated_duration_s=15.0,
            reversible=True,
        ))

    elif category == "risk_mitigate":
        # Step 1: Assess current state
        steps.append(WorkPlanStep(
            step_id=f"{action_id}.1",
            sequence=1,
            description="Assess current risk state",
            goal=f"Analyze the current state of the risk: {description}",
            risk_tier=1,
            capabilities_required=("search_query",),
            preferred_provider_id=primary_prov or "local.slice",
            verification_claims=[f"Risk '{action_id}' current state documented"],
            estimated_duration_s=30.0,
            reversible=True,
        ))

        # Step 2: Apply mitigation
        steps.append(WorkPlanStep(
            step_id=f"{action_id}.2",
            sequence=2,
            description=f"Mitigate: {description}",
            goal=f"Apply the mitigation for: {description}. "
                 f"Do not make irreversible changes. Report what was changed.",
            risk_tier=base_risk_tier,
            capabilities_required=("search_query",),
            preferred_provider_id=primary_prov or "cli.codebuddy",
            fallback_provider_ids=fallback_provs,
            verification_claims=[
                f"Mitigation applied for '{action_id}'",
                "No production impact",
            ],
            estimated_duration_s=180.0,
            reversible=False,  # mitigation may not be fully reversible
        ))

        # Step 3: Verify mitigation effectiveness
        steps.append(WorkPlanStep(
            step_id=f"{action_id}.3",
            sequence=3,
            description="Verify mitigation reduced risk",
            goal=f"Verify that the mitigation for '{description}' was effective. "
                 f"Check that risk score has decreased.",
            risk_tier=1,
            capabilities_required=(),
            preferred_provider_id=primary_prov or "local.slice",
            verification_claims=[f"Risk '{action_id}' severity reduced"],
            estimated_duration_s=30.0,
            reversible=True,
        ))

    elif category == "debt_reduce":
        # Step 1: Audit debt scope
        steps.append(WorkPlanStep(
            step_id=f"{action_id}.1",
            sequence=1,
            description="Audit the debt scope",
            goal=f"Audit the scope and impact of technical debt: {description}",
            risk_tier=1,
            capabilities_required=("search_query",),
            preferred_provider_id=primary_prov or "local.slice",
            verification_claims=[f"Debt '{action_id}' scope documented"],
            estimated_duration_s=30.0,
            reversible=True,
        ))

        # Step 2: Refactor / reduce debt
        steps.append(WorkPlanStep(
            step_id=f"{action_id}.2",
            sequence=2,
            description=f"Reduce debt: {description}",
            goal=f"Reduce the technical debt: {description}. "
                 f"Make minimal, focused changes. Run tests after each change. "
                 f"Do not change behavior — only structure.",
            risk_tier=base_risk_tier,
            capabilities_required=(),
            tainted_inputs=True,  # refactoring reads existing code
            preferred_provider_id=primary_prov or "cli.codebuddy",
            fallback_provider_ids=fallback_provs,
            verification_claims=[
                f"Debt '{action_id}' reduced",
                "All existing tests still pass",
            ],
            estimated_duration_s=300.0,
            reversible=False,  # refactoring is hard to undo
        ))

        # Step 3: Verify tests pass
        steps.append(WorkPlanStep(
            step_id=f"{action_id}.3",
            sequence=3,
            description="Verify no regressions",
            goal=f"Run the test suite to verify no regressions from the debt "
                 f"reduction: {description}",
            risk_tier=1,
            capabilities_required=(),
            preferred_provider_id=primary_prov or "local.slice",
            verification_claims=[
                f"All tests pass after debt reduction '{action_id}'",
            ],
            estimated_duration_s=60.0,
            reversible=True,
        ))

    else:
        # Generic single-step task
        steps.append(WorkPlanStep(
            step_id=f"{action_id}.1",
            sequence=1,
            description=description,
            goal=description,
            risk_tier=base_risk_tier,
            capabilities_required=(),
            preferred_provider_id=primary_prov or "local.slice",
            fallback_provider_ids=fallback_provs,
            verification_claims=[f"Action '{action_id}' completed"],
            estimated_duration_s=60.0,
            reversible=True,
        ))

    return steps


def work_plan_as_dict(plan: WorkPlan) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "source_action_id": plan.source_action_id,
        "source_action_description": plan.source_action_description,
        "category": plan.category,
        "total_steps": plan.total_steps,
        "max_risk_tier": plan.max_risk_tier,
        "requires_operator_approval": plan.requires_operator_approval,
        "approved_capabilities": sorted(plan.approved_capabilities),
        "expected_outcome": plan.expected_outcome,
        "primary_providers": plan.primary_providers,
        "evidence_chain": plan.evidence_chain,
        "steps": [
            {
                "step_id": s.step_id,
                "sequence": s.sequence,
                "description": s.description,
                "goal": s.goal,
                "risk_tier": s.risk_tier,
                "capabilities_required": list(s.capabilities_required),
                "tainted_inputs": s.tainted_inputs,
                "preferred_provider_id": s.preferred_provider_id,
                "fallback_provider_ids": s.fallback_provider_ids,
                "verification_claims": s.verification_claims,
                "estimated_duration_s": s.estimated_duration_s,
                "reversible": s.reversible,
            }
            for s in plan.steps
        ],
    }