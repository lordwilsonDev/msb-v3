"""Next-Best-Action Engine — the single best action with evidence chain.

Takes the ranked prioritization ledger and selects the top action,
then validates it against a checklist:

    1. Is the action reversible if it fails?
    2. Does it have concrete evidence backing it?
    3. Are the prerequisites satisifed?
    4. Can we route it to an available provider?
    5. What is the expected outcome?

Returns a governed recommendation — NOT a command. MSB still decides
whether to execute it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from msb_v3.plei.decisions.prioritization import (
    PrioritizationReport,
    ScoredAction,
)


@dataclass(slots=True)
class NextAction:
    """The single best next action with full evidence chain."""

    action: ScoredAction
    rank: int = 1
    reversibility: float = 0.0  # 0-1
    prerequisites_met: bool = True
    provider_available: bool = False
    provider_id: str = ""
    expected_outcome: str = ""
    validation_checks: list[str] = field(default_factory=list)
    alternative: str = ""  # fallback if this can't be executed


@dataclass(slots=True)
class NextActionReport:
    """Next-best-action with fallback chain."""

    primary: NextAction
    alternatives: list[NextAction] = field(default_factory=list)
    decision_context: str = ""


def select_next_action(
    prioritization: PrioritizationReport,
    provider_availability: dict[str, bool] | None = None,
) -> NextActionReport:
    """Select the single best next action from the ranked ledger.

    Validates the top action, then provides a fallback chain if
    prerequisites aren't met or no provider is available.
    """
    if provider_availability is None:
        provider_availability = {}

    report = NextActionReport(
        primary=_build_next_action(None, 0, "no actions available", provider_availability),
        decision_context=(
            "Next-best-action selects the highest-score action "
            "that is executable given current provider availability "
            "and prerequisite satisfaction."
        ),
    )

    alternatives: list[NextAction] = []

    for i, action in enumerate(prioritization.actions[:10]):
        na = _build_next_action(action, i + 1, "", provider_availability)

        if i == 0:
            report.primary = na

        if na.provider_available and na.prerequisites_met:
            alternatives.append(na)

        if len(alternatives) >= 3:
            break

    report.alternatives = alternatives

    # If primary isn't executable, promote first alternative
    if not report.primary.provider_available or not report.primary.prerequisites_met:
        if alternatives:
            first_alt = alternatives[0]
            first_alt.rank = 1
            report.primary = first_alt
            report.alternatives = alternatives[1:]

    return report


def _build_next_action(
    action: ScoredAction | None,
    rank: int,
    fallback_context: str,
    provider_availability: dict[str, bool],
) -> NextAction:
    """Construct a NextAction with full validation."""
    if action is None:
        return NextAction(
            action=ScoredAction(
                action_id="none",
                description=fallback_context or "no actions available",
                category="none",
                source="none",
                impact=0,
                risk_reduction=0,
                confidence=0,
                cost=0,
                score=0,
            ),
            rank=rank,
            reversibility=0,
            prerequisites_met=False,
            provider_available=False,
            expected_outcome="No actionable recommendation — all actions blocked or scored zero",
            validation_checks=["no actions in ledger"],
        )

    # Check provider availability
    prov_ids = action.recommended_providers
    prov_available = False
    available_prov = ""
    if prov_ids:
        for pid in prov_ids:
            if provider_availability.get(pid, False):
                prov_available = True
                available_prov = pid
                break
    else:
        # No specific provider needed — the action is informational
        prov_available = True
        available_prov = "n/a"

    # Prerequisites check
    prereqs_met = not action.prerequisites or all(
        provider_availability.get(p, False) for p in action.prerequisites
    )

    # Estimate reversibility
    reversibility = {
        "gap_close": 0.90,  # installing a skill is reversible
        "risk_mitigate": 0.50,  # risk mitigation may require architectural change
        "debt_reduce": 0.40,  # refactoring is hard to undo
        "capability_add": 0.85,
    }.get(action.category, 0.60)

    # Expected outcome
    if action.category == "gap_close":
        expected_outcome = (
            f"Capability {action.source} becomes available, "
            f"unblocking dependent work"
        )
    elif action.category == "risk_mitigate":
        expected_outcome = (
            f"Risk '{action.source}' severity reduced by "
            f"{action.risk_reduction:.0%}"
        )
    elif action.category == "debt_reduce":
        expected_outcome = (
            f"Debt item '{action.source}' reduced — "
            f"long-term velocity improvement"
        )
    else:
        expected_outcome = f"Action completed: {action.description}"

    # Validation checks
    checks = [
        f"Score: {action.score} (impact={action.impact} × risk_reduction={action.risk_reduction} × confidence={action.confidence} ÷ cost={action.cost})",
        f"Reversibility: {reversibility:.0%} — {'reversible' if reversibility > 0.5 else 'hard to undo'}",
        f"Evidence: {action.evidence[:120]}" if action.evidence else "Evidence: none provided",
        f"Provider: {'available' if prov_available else 'MISSING'}" + (f" ({available_prov})" if available_prov else ""),
        f"Prerequisites: {'met' if prereqs_met else 'BLOCKED'}" + (f" — {action.prerequisites}" if not prereqs_met and action.prerequisites else ""),
    ]

    return NextAction(
        action=action,
        rank=rank,
        reversibility=reversibility,
        prerequisites_met=prereqs_met,
        provider_available=prov_available,
        provider_id=available_prov,
        expected_outcome=expected_outcome,
        validation_checks=checks,
        alternative=(
            f"Next alternative: try the #{rank + 1} action"
            if rank < 10
            else "No alternatives — escalate to human"
        ),
    )


def next_action_as_dict(report: NextActionReport) -> dict[str, Any]:
    return {
        "primary": {
            "rank": report.primary.rank,
            "action_id": report.primary.action.action_id,
            "description": report.primary.action.description,
            "score": report.primary.action.score,
            "category": report.primary.action.category,
            "reversibility": report.primary.reversibility,
            "prerequisites_met": report.primary.prerequisites_met,
            "provider_available": report.primary.provider_available,
            "provider_id": report.primary.provider_id,
            "expected_outcome": report.primary.expected_outcome,
            "evidence": report.primary.action.evidence,
            "validation_checks": report.primary.validation_checks,
            "alternative": report.primary.alternative,
        },
        "alternatives": [
            {
                "rank": a.rank,
                "action_id": a.action.action_id,
                "description": a.action.description,
                "score": a.action.score,
                "provider_available": a.provider_available,
                "provider_id": a.provider_id,
            }
            for a in report.alternatives
        ],
        "decision_context": report.decision_context,
    }