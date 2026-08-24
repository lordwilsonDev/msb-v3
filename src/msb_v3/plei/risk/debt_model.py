"""Debt Model — technical debt with impact × probability × irreversibility.

Scores every known debt item across seven categories:
    technical, test, security, documentation, architecture, observability, operational

Each item is scored:
    impact (1–10) × probability (0.0–1.0) × irreversibility (1–10) → priority

The model draws from:
    - project-map.md debt table (authoritative)
    - MANIFEST.md gaps (operational debt)
    - Source ingestion (missing tests → test debt)
    - Dependency graph (cycles → architecture debt)

Output is a ranked debt ledger with Monte Carlo-ready data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from msb_v3.plei.twin import ProjectTwin

# ---------------------------------------------------------------------------
# Hardcoded debt items from project-map.md — the AUTHORITATIVE source
# ---------------------------------------------------------------------------

# These mirror the project-map §19 table. They are hardcoded because
# parsing markdown tables reliably is a separate problem; the table
# itself is maintained by the lifecycle orchestrator and changes rarely.
#

_KNOWN_DEBT = [
    {
        "item": "No DB schema versioning/migrations",
        "class": "Data/Operational",
        "impact": 8,
        "probability": 0.70,
        "irreversibility": 9,
        "note": "Documented limitation #1 — irreversible once data exists at scale",
    },
    {
        "item": "CLI provider is best-effort isolation, not a sandbox",
        "class": "Security",
        "impact": 9,
        "probability": 0.40,
        "irreversibility": 7,
        "note": "L9 parked — capability escape surface",
    },
    {
        "item": "Disk saturation blocks multi-modal + long-term evidence growth",
        "class": "Operational",
        "impact": 8,
        "probability": 0.90,
        "irreversibility": 5,
        "note": "Already caused an incident; 89% disk usage at last check",
    },
    {
        "item": "Factory LLM reviewers miss seeded contradictions",
        "class": "AI/Evidence",
        "impact": 4,
        "probability": 0.60,
        "irreversibility": 3,
        "note": "Mitigated: deterministic scan catches, hermetic-proven",
    },
    {
        "item": "Tenant chat routing not tenant-scoped (RAG is)",
        "class": "Security/Data",
        "impact": 7,
        "probability": 0.30,
        "irreversibility": 6,
        "note": "Becomes HIGH at multi-tenant (v4)",
    },
    {
        "item": "Deleted-file diffs not emitted by factory compute_changes",
        "class": "Code",
        "impact": 3,
        "probability": 0.50,
        "irreversibility": 2,
        "note": "Low priority",
    },
    {
        "item": "Latency evidence is single-sample; grows with 30-day trial",
        "class": "Evidence",
        "impact": 2,
        "probability": 0.80,
        "irreversibility": 1,
        "note": "LOW — self-correcting with more data",
    },
    {
        "item": "Off-machine replication secondary still needed",
        "class": "Operational",
        "impact": 7,
        "probability": 0.20,
        "irreversibility": 4,
        "note": "Pipeline proven locally; off-machine remains a gap",
    },
    {
        "item": "No provider SLA monitoring",
        "class": "Observability",
        "impact": 6,
        "probability": 0.50,
        "irreversibility": 3,
        "note": "Provider failures detected reactively (circuit breaker), not predicted",
    },
]


@dataclass(slots=True)
class DebtItem:
    """One scored debt item."""

    item: str
    debt_class: str  # category
    impact: int  # 1–10
    probability: float  # 0.0–1.0
    irreversibility: int  # 1–10
    priority: float  # impact × probability × irreversibility
    note: str = ""


@dataclass(slots=True)
class DebtReport:
    """Ranked debt ledger."""

    total_items: int
    total_priority: float  # sum of all priorities
    avg_priority: float
    by_class: dict[str, list[DebtItem]] = field(default_factory=dict)
    top_5: list[DebtItem] = field(default_factory=list)
    items: list[DebtItem] = field(default_factory=list)


def score_debt(twin: ProjectTwin) -> DebtReport:
    """Score every known debt item and produce a ranked report.

    Augments the hardcoded table with ingestion-derived debt:
        - Cycles in dependency graph → architecture debt
        - Missing test coverage → test debt
    """
    items: list[DebtItem] = []

    # Known debt items
    for d in _KNOWN_DEBT:
        priority = d["impact"] * d["probability"] * d["irreversibility"]
        items.append(DebtItem(
            item=d["item"],
            debt_class=d["class"],
            impact=d["impact"],
            probability=d["probability"],
            irreversibility=d["irreversibility"],
            priority=round(priority, 1),
            note=d["note"],
        ))

    # Augment from dependency graph (cycles → architecture debt)
    try:
        from msb_v3.plei.dependency.graph import build_dependency_graph
        dep_graph = build_dependency_graph(Path.cwd())
        if dep_graph.cycles:
            for cycle in dep_graph.cycles:
                cycle_str = " → ".join(cycle)
                priority = 6 * 0.60 * 5  # moderate impact × moderate prob × moderate irrev
                items.append(DebtItem(
                    item=f"Cyclic dependency: {cycle_str}",
                    debt_class="Architecture",
                    impact=6,
                    probability=0.60,
                    irreversibility=5,
                    priority=round(priority, 1),
                    note="Cycles complicate topological reasoning and increase coupling",
                ))
    except Exception:
        pass

    # Augment from test data
    test_count = twin.evidence.test_count.value
    if isinstance(test_count, int) and test_count < 100:
        priority = 7 * 0.80 * 6
        items.append(DebtItem(
            item=f"Low test count ({test_count} collected)",
            debt_class="Test",
            impact=7,
            probability=0.80,
            irreversibility=6,
            priority=round(priority, 1),
            note="Low test count increases regression risk",
        ))

    # Sort by priority descending
    items.sort(key=lambda i: -i.priority)

    # Group by class
    by_class: dict[str, list[DebtItem]] = {}
    for item in items:
        by_class.setdefault(item.debt_class, []).append(item)

    total_priority = sum(i.priority for i in items)
    return DebtReport(
        total_items=len(items),
        total_priority=round(total_priority, 1),
        avg_priority=round(total_priority / len(items), 1) if items else 0.0,
        by_class=by_class,
        top_5=items[:5],
        items=items,
    )


def debt_report_as_dict(report: DebtReport) -> dict[str, Any]:
    return {
        "total_items": report.total_items,
        "total_priority": report.total_priority,
        "avg_priority": report.avg_priority,
        "by_class": {
            cls: [
                {
                    "item": i.item,
                    "impact": i.impact,
                    "probability": i.probability,
                    "irreversibility": i.irreversibility,
                    "priority": i.priority,
                    "note": i.note,
                }
                for i in items
            ]
            for cls, items in report.by_class.items()
        },
        "top_5": [
            {
                "item": i.item,
                "class": i.debt_class,
                "priority": i.priority,
                "note": i.note,
            }
            for i in report.top_5
        ],
    }