"""PLEI API — FastAPI router under /plei.

Endpoints:
    GET  /plei/understand   → project summary
    GET  /plei/status       → lifecycle + health
    GET  /plei/lifecycle    → detailed lifecycle classification
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from msb_v3.plei.lifecycle import classify_lifecycle, lifecycle_as_dict
from msb_v3.plei.orchestrator import ingest_all, twin_summary

plei_router = APIRouter(prefix="/plei", tags=["plei"])

_DEFAULT_ROOT = str(Path(__file__).resolve().parents[3])  # repo root from src/msb_v3/plei


@plei_router.get("/understand", summary="Reconstruct project from evidence")
async def understand_project(project_root: str = Query(default=_DEFAULT_ROOT, description="Project root path")):
    """Ingest all seven layers and return the complete project understanding.

    This is the primary endpoint: README, source, tests, config, deps,
    evidence, and repo state — every assertion carries a provenance tag.
    """
    try:
        twin = ingest_all(project_root)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}") from exc
    return twin_summary(twin)


@plei_router.get("/status", summary="Lifecycle + project health")
async def project_status(project_root: str = Query(default=_DEFAULT_ROOT, description="Project root path")):
    """Return lifecycle position and health scores."""
    twin = ingest_all(project_root)
    lc = classify_lifecycle(twin)
    return {
        "lifecycle": lifecycle_as_dict(lc),
        "health": twin.health.scores.as_dict(),
        "risks": twin.health.risks.as_dict(),
        "missing_capabilities": twin.health.missing_capabilities.as_dict(),
    }


@plei_router.get("/lifecycle", summary="Detailed lifecycle classification")
async def project_lifecycle(project_root: str = Query(default=_DEFAULT_ROOT, description="Project root path")):
    """Classify lifecycle with full evidence and subsystem breakdown."""
    twin = ingest_all(project_root)
    lc = classify_lifecycle(twin)
    return lifecycle_as_dict(lc)


@plei_router.get("/gaps", summary="Capability gaps for current lifecycle stage")
async def project_gaps(project_root: str = Query(default=_DEFAULT_ROOT, description="Project root path")):
    """Detect capability gaps: what the project needs vs what's available."""
    from msb_v3.plei.engineering.gap_detector import detect_gaps, gap_report_as_dict
    twin = ingest_all(project_root)
    report = detect_gaps(twin)
    return gap_report_as_dict(report)


@plei_router.get("/capabilities", summary="Capability graph for the project's lifecycle")
async def project_capabilities(project_root: str = Query(default=_DEFAULT_ROOT, description="Project root path")):
    """What capabilities does this lifecycle stage require, and which skills provide them?"""
    from msb_v3.plei.engineering.capability_graph import graph_summary
    twin = ingest_all(project_root)
    lc = classify_lifecycle(twin)
    return graph_summary(lc.stage)


@plei_router.get("/skills", summary="Skill taxonomy — all installed skills")
async def skill_taxonomy():
    """Catalog of every installed skill with its capability bindings."""
    from msb_v3.plei.engineering.skill_taxonomy import taxonomy_summary
    return taxonomy_summary()


@plei_router.get("/risk", summary="Risk report — dependencies, failure modes, debt")
async def project_risk(project_root: str = Query(default=_DEFAULT_ROOT, description="Project root path")):
    """Unified risk: dependency bottlenecks, failure modes, technical debt."""
    from msb_v3.plei.risk.report import analyze_risk, risk_report_as_dict
    twin = ingest_all(project_root)
    report = analyze_risk(twin)
    return risk_report_as_dict(report)


@plei_router.get("/debt", summary="Technical debt — ranked by impact × probability × irreversibility")
async def project_debt():
    """Ranked technical debt ledger with Monte Carlo-ready scoring."""
    from msb_v3.plei.risk.debt_model import debt_report_as_dict, score_debt
    twin = ingest_all()
    report = score_debt(twin)
    return debt_report_as_dict(report)


@plei_router.get("/dependencies", summary="Dependency graph — critical path, bottlenecks, coupling")
async def project_dependencies():
    """Module-level dependency graph with critical path and coupling metrics."""
    from pathlib import Path

    from msb_v3.plei.dependency.graph import (
        build_dependency_graph,
        dependency_graph_as_dict,
    )
    graph = build_dependency_graph(Path(__file__).resolve().parents[3])
    return dependency_graph_as_dict(graph)