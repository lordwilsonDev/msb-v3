"""Tradeoff Analysis — head-to-head alternative comparison.

Takes a set of scenarios (baseline + options) and produces a tradeoff table:
score, risk profile, cost, confidence, reversibility. The decision engine
uses this to explain *why* one option beats another.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TradeoffOption:
    """One alternative in a tradeoff comparison."""

    name: str
    description: str
    score: float = 0.0  # composite utility
    risk_reduction: float = 0.0
    confidence: float = 0.0
    cost: float = 0.0
    reversibility: float = 0.0  # 0-1: how easy to undo
    evidence: str = ""
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TradeoffReport:
    """Side-by-side comparison of alternatives."""

    baseline: TradeoffOption
    options: list[TradeoffOption] = field(default_factory=list)
    recommendation: str = ""
    decision_rule: str = ""


def compare_tradeoffs(
    gap_dict: dict[str, Any],
    risk_dict: dict[str, Any],
) -> TradeoffReport:
    """Build tradeoff options from the current project state.

    Produces:
      - BASELINE: do nothing (no gap closures, no risk mitigation)
      - OPTION A: close all MISSING gaps
      - OPTION B: mitigate top-3 risks
      - OPTION C: address top-3 debt items
      - OPTION D: balanced (top gap + top risk + top debt)
    """
    gaps = gap_dict.get("gaps", [])
    missing_gaps = [g for g in gaps if isinstance(g, dict) and g.get("status") == "MISSING"]
    partial_gaps = [g for g in gaps if isinstance(g, dict) and g.get("status") == "PARTIAL"]
    top_risks = risk_dict.get("top_risks", [])[:5]
    debt = risk_dict.get("debt_report", {})
    top_debt = debt.get("top_5", [])[:5] if isinstance(debt.get("top_5"), list) else []

    # --- BASELINE ---
    baseline = TradeoffOption(
        name="BASELINE",
        description="No action — continue with current gaps, risks, and debt",
        score=0.0,
        risk_reduction=0.0,
        confidence=1.0,
        cost=0.0,
        reversibility=1.0,
        evidence="Current state as measured by PLEI",
        pros=["Zero effort", "Zero disruption"],
        cons=[
            f"{len(missing_gaps)} capability gaps remain open",
            f"{len(top_risks)} risks unmitigated",
            f"{len(top_debt)} debt items unaddressed",
        ],
    )

    # --- OPTION A: close gaps ---
    gap_score = sum(
        g.get("criticality", 5) * (0.7 if g.get("status") == "MISSING" else 0.35)
        for g in missing_gaps + partial_gaps
    )
    gap_confidence = 0.85 if missing_gaps else 0.70
    gap_cost = len(missing_gaps) * 2.0 + len(partial_gaps) * 1.5
    option_a = TradeoffOption(
        name="OPTION A: Close Gaps",
        description=f"Install/activate {'all' if len(missing_gaps) < 5 else 'top'} missing capabilities",
        score=round(gap_score / max(1.0, gap_cost), 1),
        risk_reduction=min(0.80, 0.10 + 0.15 * len(missing_gaps)),
        confidence=gap_confidence,
        cost=gap_cost,
        reversibility=0.90,
        evidence=f"{len(missing_gaps)} MISSING, {len(partial_gaps)} PARTIAL capabilities",
        pros=[
            "Directly addresses root capability deficits",
            "Enables future work that's currently blocked",
        ],
        cons=[
            "Skill installation may require API keys",
            "New capabilities need verification",
        ],
    )

    # --- OPTION B: mitigate top risks ---
    risk_score = sum(
        r.get("severity", 5) * r.get("likelihood", 0.3)
        for r in top_risks[:3] if isinstance(r, dict)
    )
    risk_cost = sum(
        max(1.0, r.get("severity", 5) / 2.5)
        for r in top_risks[:3] if isinstance(r, dict)
    )
    option_b = TradeoffOption(
        name="OPTION B: Mitigate Risks",
        description="Address top-3 risks: reduce likelihood or severity",
        score=round(risk_score / max(1.0, risk_cost), 1),
        risk_reduction=min(0.85, 0.15 * len(top_risks[:3])),
        confidence=0.75,
        cost=risk_cost,
        reversibility=0.60,
        evidence=f"{len(top_risks[:3])} risks targeted",
        pros=[
            "Reduces chance of project-derailing incidents",
            "Sensitivity analysis may reveal compounding effects",
        ],
        cons=[
            "Hard to measure risk reduction before it's tested",
            "Some risks require architectural changes",
        ],
    )

    # --- OPTION C: reduce debt ---
    debt_score = sum(
        d.get("priority", 2.0) * d.get("irreversibility", 0.5)
        for d in top_debt[:3] if isinstance(d, dict)
    )
    debt_cost = sum(
        max(1.0, d.get("impact", 5) / 2.0)
        for d in top_debt[:3] if isinstance(d, dict)
    )
    option_c = TradeoffOption(
        name="OPTION C: Reduce Debt",
        description="Address top-3 debt items by impact × irreversibility",
        score=round(debt_score / max(1.0, debt_cost), 1),
        risk_reduction=min(0.70, 0.10 * len(top_debt[:3])),
        confidence=0.80,
        cost=debt_cost,
        reversibility=0.50,
        evidence=f"{len(top_debt[:3])} debt items targeted",
        pros=[
            "Reduces long-term drag on velocity",
            "Debt is well-scoped and measurable",
        ],
        cons=[
            "Debt reduction often requires refactoring",
            "May not address immediate blockers",
        ],
    )

    # --- OPTION D: balanced ---
    balanced_score = (gap_score * 0.4 + risk_score * 0.35 + debt_score * 0.25)
    balanced_cost = (gap_cost * 0.4 + risk_cost * 0.35 + debt_cost * 0.25)
    option_d = TradeoffOption(
        name="OPTION D: Balanced",
        description="Top gap + top risk + top debt — one from each category",
        score=round(balanced_score / max(1.0, balanced_cost), 1),
        risk_reduction=min(0.75, 0.10 + 0.10 * (len(missing_gaps) + len(top_risks[:1]) + len(top_debt[:1]))),
        confidence=0.78,
        cost=balanced_cost,
        reversibility=0.70,
        evidence="Weighted combination of the three most impactful actions",
        pros=[
            "Diversified across gaps, risks, and debt",
            "No single-point investment",
        ],
        cons=[
            "Slower closure on any single category",
            "Requires context-switching",
        ],
    )

    options = [option_a, option_b, option_c, option_d]
    options.sort(key=lambda o: -o.score)

    # --- Decision rule ---
    best = options[0]
    recommendation = (
        f"Recommend {best.name}: score={best.score}, "
        f"risk reduction={best.risk_reduction:.0%}, confidence={best.confidence:.0%}"
    )

    return TradeoffReport(
        baseline=baseline,
        options=options,
        recommendation=recommendation,
        decision_rule=(
            "Score = (impact × risk_reduction × confidence) / cost. "
            "Highest score wins. Reversibility provided for risk-aware decisions."
        ),
    )


def tradeoff_as_dict(report: TradeoffReport) -> dict[str, Any]:
    return {
        "baseline": {
            "name": report.baseline.name,
            "description": report.baseline.description,
            "score": report.baseline.score,
            "risk_reduction": report.baseline.risk_reduction,
            "confidence": report.baseline.confidence,
            "cost": report.baseline.cost,
            "reversibility": report.baseline.reversibility,
            "evidence": report.baseline.evidence,
            "pros": report.baseline.pros,
            "cons": report.baseline.cons,
        },
        "options": [
            {
                "name": o.name,
                "description": o.description,
                "score": o.score,
                "risk_reduction": o.risk_reduction,
                "confidence": o.confidence,
                "cost": o.cost,
                "reversibility": o.reversibility,
                "evidence": o.evidence,
                "pros": o.pros,
                "cons": o.cons,
            }
            for o in report.options
        ],
        "recommendation": report.recommendation,
        "decision_rule": report.decision_rule,
    }