"""Action Prioritization — score every possible action.

Score(a) = Impact(a) × RiskReduction(a) × Confidence(a) ÷ Cost(a)

This takes the gap report + risk report + simulation output and assigns
a numeric score to every possible engineering action. The output is a
ranked action ledger — the primary input to the Next-Best-Action engine.

Every score decomposes into sub-scores so the ranking is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ScoredAction:
    """One action with decomposed scoring."""

    action_id: str
    description: str
    category: str  # "gap_close" | "risk_mitigate" | "debt_reduce" | "capability_add"
    source: str  # which gap / risk / debt item this addresses

    impact: float  # 1–10, how much does this improve the project?
    risk_reduction: float  # 0–1, fraction of risk eliminated
    confidence: float  # 0–1, how certain are we this helps?
    cost: float  # 1–10, estimated effort/cost (higher = more expensive)

    score: float = 0.0  # composite: impact * risk_reduction * confidence / cost

    evidence: str = ""
    prerequisites: list[str] = field(default_factory=list)
    recommended_providers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PrioritizationReport:
    """Ranked action ledger."""

    total_actions: int
    actions: list[ScoredAction] = field(default_factory=list)
    top_action: ScoredAction | None = None


def prioritize(
    gap_dict: dict[str, Any],
    risk_dict: dict[str, Any],
    sim_sensitivity: dict[str, Any] | None = None,
) -> PrioritizationReport:
    """Score every possible action from gaps + risks + sensitivity.

    Actions come from three sources:
      1. Gap closures — install missing skills
      2. Risk mitigations — reduce top risk items
      3. Debt reductions — address top debt items

    Sensitivity analysis (from Phase 4) refines impact scoring:
    variables with high variance contribution get higher impact.
    """
    actions: list[ScoredAction] = []

    # --- Source 1: Capability gaps → install skills ---
    for gap in gap_dict.get("gaps", []):
        if not isinstance(gap, dict):
            continue
        status = gap.get("status", "COVERED")
        if status == "COVERED":
            continue

        cap_name = gap.get("capability", "unknown")
        criticality = gap.get("criticality", 5)

        # Impact: criticality normalized to 1–10
        impact = min(10.0, max(1.0, float(criticality)))
        # Risk reduction: MISSING gaps block progress fully
        risk_reduction = 0.70 if status == "MISSING" else 0.35
        # Confidence: we know what skill provides this
        confidence = 0.85 if gap.get("missing_skills") or gap.get("available_skills") else 0.60
        # Cost: installing a skill is cheap
        cost = 2.0 if status == "MISSING" else 1.5

        score = (impact * risk_reduction * confidence) / cost

        actions.append(ScoredAction(
            action_id=f"gap:{cap_name}",
            description=f"{'Install' if status == 'MISSING' else 'Activate'} capability: {cap_name}",
            category="gap_close",
            source=cap_name,
            impact=impact,
            risk_reduction=risk_reduction,
            confidence=confidence,
            cost=cost,
            score=round(score, 2),
            evidence=gap.get("recommendation", ""),
            recommended_providers=gap.get("available_skills", []),
        ))

    # --- Source 2: Top risks → mitigate ---
    for risk in risk_dict.get("top_risks", [])[:8]:
        if not isinstance(risk, dict):
            continue
        severity = float(risk.get("severity", 5))
        likelihood = float(risk.get("likelihood", 0.3))
        score_r = float(risk.get("risk_score", 3.0))

        # Impact: risk score normalized
        impact = min(10.0, max(1.0, score_r))
        # Risk reduction: proportional to likelihood (high likelihood = more reducible)
        risk_reduction = min(0.90, likelihood * 1.2)
        # Confidence: source-dependent
        source = risk.get("source", "debt")
        confidence = {"failure": 0.75, "dependency": 0.70, "debt": 0.80}.get(source, 0.70)
        # Cost: severity-based (higher severity = harder)
        cost = max(1.0, min(10.0, severity / 2.5))

        final_score = (impact * risk_reduction * confidence) / cost

        risk_desc = risk.get("description", "unknown risk")
        actions.append(ScoredAction(
            action_id=f"risk:{risk_desc[:50]}",
            description=f"Mitigate: {risk_desc}",
            category="risk_mitigate",
            source=source,
            impact=impact,
            risk_reduction=risk_reduction,
            confidence=confidence,
            cost=cost,
            score=round(final_score, 2),
            evidence=risk.get("details", ""),
        ))

    # --- Source 3: Top debt items → reduce ---
    debt = risk_dict.get("debt_report", {})
    for item in (debt.get("top_5", []) or []):
        if not isinstance(item, dict):
            continue
        priority = float(item.get("priority", 2.0))
        impact_d = float(item.get("impact", 5))
        irreversibility = float(item.get("irreversibility", 0.5))

        # Impact: debt priority * impact
        impact = min(10.0, max(1.0, (priority * impact_d) / 3.0))
        # Risk reduction: irreversibility (hard-to-reverse debt is worth reducing)
        risk_reduction = irreversibility
        # Confidence: debt items are well-scoped
        confidence = 0.80
        # Cost: higher impact = harder to fix
        cost = max(1.0, min(10.0, impact_d / 2.0))

        final_score = (impact * risk_reduction * confidence) / cost

        desc = item.get("item", "unknown debt")
        actions.append(ScoredAction(
            action_id=f"debt:{desc[:50]}",
            description=f"Reduce debt: {desc}",
            category="debt_reduce",
            source=item.get("debt_class", "technical"),
            impact=impact,
            risk_reduction=risk_reduction,
            confidence=confidence,
            cost=cost,
            score=round(final_score, 2),
            evidence=item.get("note", ""),
        ))

    # --- Source 4: Sensitivity-informed reweighting ---
    if sim_sensitivity:
        tornado_entries = sim_sensitivity.get("tornado", [])
        if tornado_entries:
            # Boost actions that address high-sensitivity variables
            for entry in tornado_entries[:5]:
                if not isinstance(entry, dict):
                    continue
                var_name = entry.get("variable", "")
                contribution = float(entry.get("contribution_pct", 0)) / 100.0
                # Find matching actions and boost their score
                for action in actions:
                    if var_name.lower() in action.source.lower() or var_name.lower() in action.description.lower():
                        action.score = round(action.score * (1.0 + contribution * 0.5), 2)

    # Sort by score descending
    actions.sort(key=lambda a: -a.score)

    top = actions[0] if actions else None

    return PrioritizationReport(
        total_actions=len(actions),
        actions=actions,
        top_action=top,
    )


def prioritization_as_dict(report: PrioritizationReport) -> dict[str, Any]:
    return {
        "total_actions": report.total_actions,
        "top_action": {
            "action_id": report.top_action.action_id,
            "description": report.top_action.description,
            "score": report.top_action.score,
            "evidence": report.top_action.evidence,
        } if report.top_action else None,
        "actions": [
            {
                "action_id": a.action_id,
                "description": a.description,
                "category": a.category,
                "source": a.source,
                "impact": a.impact,
                "risk_reduction": a.risk_reduction,
                "confidence": a.confidence,
                "cost": a.cost,
                "score": a.score,
                "evidence": a.evidence,
                "prerequisites": a.prerequisites,
                "recommended_providers": a.recommended_providers,
            }
            for a in report.actions[:15]
        ],
    }