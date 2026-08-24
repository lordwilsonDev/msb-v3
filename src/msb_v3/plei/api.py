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


@plei_router.get("/simulate", summary="Monte Carlo simulation — probabilistic forecast")
async def project_simulate(project_root: str = Query(default=_DEFAULT_ROOT, description="Project root path"), trials: int = Query(default=2000, ge=100, le=50000, description="Number of Monte Carlo trials")):
    """Run Monte Carlo simulation from risk data and return forecast."""
    from msb_v3.plei.engineering.gap_detector import detect_gaps, gap_report_as_dict
    from msb_v3.plei.risk.report import analyze_risk, risk_report_as_dict
    from msb_v3.plei.simulation.forecast import build_forecast, forecast_as_dict
    from msb_v3.plei.simulation.monte_carlo import (
        SimConfig,
        monte_carlo_as_dict,
        run_monte_carlo,
        variables_from_gaps,
        variables_from_risk_report,
    )

    twin = ingest_all(project_root)
    risk_dict = risk_report_as_dict(analyze_risk(twin))
    gaps = detect_gaps(twin)
    gap_dict = gap_report_as_dict(gaps)

    vars_risk = variables_from_risk_report(risk_dict)
    vars_gaps = variables_from_gaps(gap_dict)

    config = SimConfig(variables=vars_risk + vars_gaps, trial_count=trials, seed=42, base_duration_days=30.0)
    mc = run_monte_carlo(config, seed=42, trial_count=trials)
    forecast = build_forecast(mc, project_name=twin.identity.name.value or "project")

    return {
        "monte_carlo": monte_carlo_as_dict(mc),
        "forecast": forecast_as_dict(forecast),
    }


@plei_router.get("/what-if", summary="What-if scenarios vs baseline")
async def project_what_if(project_root: str = Query(default=_DEFAULT_ROOT, description="Project root path")):
    """Run what-if scenarios: fix top debt, zero failures, half failures, close gaps, pessimistic."""
    from msb_v3.plei.engineering.gap_detector import detect_gaps, gap_report_as_dict
    from msb_v3.plei.risk.report import analyze_risk, risk_report_as_dict
    from msb_v3.plei.simulation.monte_carlo import (
        SimConfig,
        variables_from_gaps,
        variables_from_risk_report,
    )
    from msb_v3.plei.simulation.scenarios import (
        run_what_if,
        scenarios_from_risk,
        what_if_as_dict,
    )

    twin = ingest_all(project_root)
    risk_dict = risk_report_as_dict(analyze_risk(twin))
    gaps = detect_gaps(twin)
    gap_dict = gap_report_as_dict(gaps)

    vars_risk = variables_from_risk_report(risk_dict)
    vars_gaps = variables_from_gaps(gap_dict)
    config = SimConfig(variables=vars_risk + vars_gaps, trial_count=2000, seed=42, base_duration_days=30.0)

    scenarios = scenarios_from_risk(risk_dict)
    report = run_what_if(config, scenarios, seed=42, trial_count=2000)
    return what_if_as_dict(report)


@plei_router.get("/sensitivity", summary="Sensitivity analysis — which variables drive uncertainty?")
async def project_sensitivity():
    """Tornado analysis: measure each variable's contribution to outcome variance."""
    from msb_v3.plei.engineering.gap_detector import detect_gaps, gap_report_as_dict
    from msb_v3.plei.risk.report import analyze_risk, risk_report_as_dict
    from msb_v3.plei.simulation.monte_carlo import (
        SimConfig,
        variables_from_gaps,
        variables_from_risk_report,
    )
    from msb_v3.plei.simulation.sensitivity import (
        analyze_sensitivity,
        sensitivity_as_dict,
    )

    twin = ingest_all()
    risk_dict = risk_report_as_dict(analyze_risk(twin))
    gaps = detect_gaps(twin)
    gap_dict = gap_report_as_dict(gaps)

    vars_risk = variables_from_risk_report(risk_dict)
    vars_gaps = variables_from_gaps(gap_dict)
    config = SimConfig(variables=vars_risk + vars_gaps, trial_count=2000, seed=42, base_duration_days=30.0)

    report = analyze_sensitivity(config, seed=42, trial_count=2000)
    return sensitivity_as_dict(report)

@plei_router.get("/decide", summary="Full decision pipeline — prioritize, tradeoffs, next action, provider routing")
async def project_decide(project_root: str = Query(default=_DEFAULT_ROOT, description="Project root path")):
    """Run the complete Phase 5 decision engine."""
    from msb_v3.plei.decisions.next_action import (
        next_action_as_dict,
        select_next_action,
    )
    from msb_v3.plei.decisions.prioritization import (
        prioritization_as_dict,
        prioritize,
    )
    from msb_v3.plei.decisions.provider_selection import (
        ProviderReport,
        build_profiles,
        provider_report_as_dict,
        select_provider_for_task,
    )
    from msb_v3.plei.decisions.tradeoffs import (
        compare_tradeoffs,
        tradeoff_as_dict,
    )
    from msb_v3.plei.engineering.gap_detector import detect_gaps, gap_report_as_dict
    from msb_v3.plei.risk.report import analyze_risk, risk_report_as_dict

    twin = ingest_all(project_root)
    risk_dict = risk_report_as_dict(analyze_risk(twin))
    gaps = detect_gaps(twin)
    gap_dict = gap_report_as_dict(gaps)

    prio = prioritize(gap_dict, risk_dict)
    tradeoffs = compare_tradeoffs(gap_dict, risk_dict)

    profiles = build_profiles()
    prov_avail: dict[str, bool] = {p.provider_id: p.available for p in profiles}

    next_report = select_next_action(prio, prov_avail)
    top_na = next_report.primary if next_report else None

    provider_sel = None
    if top_na and top_na.action:
        provider_sel = select_provider_for_task(
            task_description=top_na.action.description,
            required_capabilities=top_na.action.recommended_providers,
            max_risk_tier=4,
            profiles=profiles,
        )

    return {
        "prioritization": prioritization_as_dict(prio),
        "tradeoffs": tradeoff_as_dict(tradeoffs),
        "next_action": next_action_as_dict(next_report),
        "providers": provider_report_as_dict(ProviderReport(
            profiles=profiles,
            available_count=sum(1 for p in profiles if p.available),
            total_count=len(profiles),
            selections=[provider_sel] if provider_sel else [],
        )),
    }


@plei_router.get("/providers", summary="Provider profiles and selection intelligence")
async def project_providers():
    """Live provider profiles with success rate, latency, and specialization estimates."""
    from msb_v3.plei.decisions.provider_selection import (
        ProviderReport,
        build_profiles,
        provider_report_as_dict,
    )
    profiles = build_profiles()
    report = ProviderReport(
        profiles=profiles,
        available_count=sum(1 for p in profiles if p.available),
        total_count=len(profiles),
    )
    return provider_report_as_dict(report)
