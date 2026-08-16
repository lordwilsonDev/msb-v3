"""Software Factory planner (spec §4.2.6 — plan stage; §8 Agent 1).

Deterministic decomposition of the issue into ordered implementation
steps, each with verifiable acceptance criteria. The plan carries risks
and assumptions from an independent MoIE inversion of the issue — the
planner never plans on faith.
"""

from __future__ import annotations

from typing import List, Optional

from msb_v3.factory.models import Classification, Issue, Plan, PlanStep
from msb_v3.moie import MoIEController


def plan(
    issue: Issue,
    classification: Classification,
    *,
    moie: Optional[MoIEController] = None,
    high_impact: Optional[bool] = None,
) -> Plan:
    goal = f"{issue.title}: {issue.body}".strip()
    steps: List[PlanStep] = []

    if classification.issue_type == "bug":
        steps = [
            PlanStep("s1", "Reproduce", "Reproduce the failure with a minimal, deterministic case.", ["reproduction is captured as a test/evidence"]),
            PlanStep("s2", "Implement fix", "Change the code so the reproduction no longer fails.", ["the reported symptom is gone in the worktree"]),
            PlanStep("s3", "Add regression test", "Add a test that would have caught the bug.", ["the new test exists and fails before the fix, passes after"]),
        ]
    elif classification.issue_type == "security":
        steps = [
            PlanStep("s1", "Scope the exposure", "Confirm the vulnerable surface and its callers.", ["the attack surface is enumerated in the review"]),
            PlanStep("s2", "Implement hardening", "Close the vulnerability without breaking existing behavior.", ["the exploit path no longer works in the worktree"]),
            PlanStep("s3", "Add adversarial test", "Add a test that proves the exploit is closed.", ["the adversarial test passes"]),
        ]
    elif classification.issue_type == "refactor":
        steps = [
            PlanStep("s1", "Pin behavior", "Capture current behavior with tests before touching code.", ["existing tests pass before the refactor"]),
            PlanStep("s2", "Refactor", "Restructure the code to the intended shape.", ["public behavior is unchanged (existing tests still pass)"]),
            PlanStep("s3", "Verify", "Run the full suite and confirm no drift.", ["full suite passes in the worktree"]),
        ]
    else:  # feature / other
        steps = [
            PlanStep("s1", "Implement", "Implement the requested capability per the issue.", ["the requested behavior exists in the worktree"]),
            PlanStep("s2", "Wire integration", "Connect the capability to the surrounding system.", ["the integration point is present and callable"]),
            PlanStep("s3", "Test", "Add tests covering the new capability.", ["new tests pass"]),
        ]

    # Acceptance criteria are a superset of the step criteria.
    acceptance = ["all planned steps implemented", "test evidence exists", "independent review not BLOCK"]
    for s in steps:
        acceptance += s.acceptance
    steps.append(PlanStep("final", "Acceptance", "Confirm every acceptance criterion with evidence.", acceptance))

    # MoIE inversion of the issue feeds the plan's risks + assumptions.
    risks: List[str] = []
    assumptions: List[str] = []
    try:
        moie = moie or MoIEController()
        decision = moie.analyze(goal, context={"high_impact": high_impact if high_impact is not None else classification.severity in ("high", "critical")})
        risks = [r for r in decision.recommended_actions][:4] or []
        assumptions = [a.text[:160] for a in decision.assumptions][:4]
    except Exception:  # noqa: BLE001 — a broken MoIE must not kill the planner
        risks = ["MoIE inversion unavailable — plan carries no independent risk signal"]

    return Plan(goal=goal, steps=steps, risks=risks, assumptions=assumptions)
