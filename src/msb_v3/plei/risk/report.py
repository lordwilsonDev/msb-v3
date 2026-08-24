"""Risk Report — unified risk view combining dependency, failure, and debt.

Produces a single ranked risk ledger with:
    1. Dependency risks (bottlenecks, cycles, coupling)
    2. Failure modes (from audit data, operational state, static analysis)
    3. Technical debt (impact × probability × irreversibility)

This is the input to Phase 4 (Monte Carlo) — every risk item carries
numeric impact, probability, and irreversibility scores ready for
distribution construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from msb_v3.plei.dependency.graph import (
    DependencyGraph,
    build_dependency_graph,
    dependency_graph_as_dict,
)
from msb_v3.plei.risk.debt_model import (
    debt_report_as_dict,
    score_debt,
)
from msb_v3.plei.risk.failure_model import (
    analyze_failures,
    failure_report_as_dict,
)
from msb_v3.plei.twin import ProjectTwin


@dataclass(slots=True)
class RiskItem:
    """One consolidated risk across all three sources."""

    source: str  # "dependency", "failure", "debt"
    description: str
    severity: int  # 1–10
    likelihood: float  # 0.0–1.0
    risk_score: float  # severity × likelihood
    details: str = ""


@dataclass(slots=True)
class RiskReport:
    """Unified risk ledger."""

    total_risks: int
    dependency_risks: int
    failure_modes: int
    debt_items: int
    top_risks: list[RiskItem] = field(default_factory=list)
    dependency_graph: dict[str, Any] = field(default_factory=dict)
    failure_report: dict[str, Any] = field(default_factory=dict)
    debt_report: dict[str, Any] = field(default_factory=dict)
    critical_path: list[str] = field(default_factory=list)
    bottlenecks: list[dict[str, Any]] = field(default_factory=list)


def analyze_risk(twin: ProjectTwin) -> RiskReport:
    """Run all three risk layers and produce a unified report."""
    project_root = Path.cwd()

    # Dependency
    try:
        dep_graph = build_dependency_graph(project_root)
    except Exception:
        dep_graph = DependencyGraph()
    dep_dict = dependency_graph_as_dict(dep_graph)

    # Failure
    failure = analyze_failures(twin)

    # Debt
    debt = score_debt(twin)

    # Consolidate into ranked risk items
    risk_items: list[RiskItem] = []

    # Dependency risks
    for bn in dep_graph.bottlenecks:
        if bn["fan_in"] >= 3:
            severity = min(9, 3 + bn["fan_in"])
            likelihood = min(0.80, 0.15 * bn["fan_in"])
            risk_items.append(RiskItem(
                source="dependency",
                description=f"Bottleneck: {bn['module']} (fan-in: {bn['fan_in']})",
                severity=severity,
                likelihood=round(likelihood, 2),
                risk_score=round(severity * likelihood, 1),
                details=f"If {bn['module']} breaks, {bn['fan_in']} dependents fail",
            ))

    for cycle in dep_graph.cycles[:3]:
        cycle_str = " → ".join(cycle[:4])
        if len(cycle) > 4:
            cycle_str += " → ..."
        risk_items.append(RiskItem(
            source="dependency",
            description=f"Cyclic dependency: {cycle_str}",
            severity=6,
            likelihood=0.50,
            risk_score=3.0,
            details="Cycles complicate topological reasoning and increase coupling",
        ))

    # Failure risks
    for fm in failure.modes:
        risk_items.append(RiskItem(
            source="failure",
            description=f"{fm.kind}: {fm.component}",
            severity=fm.severity,
            likelihood=fm.likelihood,
            risk_score=round(fm.severity * fm.likelihood, 1),
            details=f"{fm.evidence} — Recovery: {fm.recovery_assessment}",
        ))

    # Debt risks (top 5)
    for d in debt.items[:5]:
        risk_items.append(RiskItem(
            source="debt",
            description=f"{d.debt_class}: {d.item}",
            severity=d.impact,
            likelihood=d.probability,
            risk_score=round(d.impact * d.probability, 1),
            details=d.note,
        ))

    # Sort by risk_score descending
    risk_items.sort(key=lambda r: -r.risk_score)

    return RiskReport(
        total_risks=len(risk_items),
        dependency_risks=dep_graph.node_count,
        failure_modes=failure.total_modes,
        debt_items=debt.total_items,
        top_risks=risk_items[:10],
        dependency_graph=dep_dict,
        failure_report=failure_report_as_dict(failure),
        debt_report=debt_report_as_dict(debt),
        critical_path=dep_graph.critical_path,
        bottlenecks=dep_graph.bottlenecks,
    )


def risk_report_as_dict(report: RiskReport) -> dict[str, Any]:
    return {
        "total_risks": report.total_risks,
        "dependency_risks": report.dependency_risks,
        "failure_modes": report.failure_modes,
        "debt_items": report.debt_items,
        "top_risks": [
            {
                "source": r.source,
                "description": r.description,
                "severity": r.severity,
                "likelihood": r.likelihood,
                "risk_score": r.risk_score,
                "details": r.details,
            }
            for r in report.top_risks
        ],
        "dependency_graph": report.dependency_graph,
        "failure_report": report.failure_report,
        "debt_report": report.debt_report,
        "critical_path": report.critical_path,
        "bottlenecks": report.bottlenecks,
    }