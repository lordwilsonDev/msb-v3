"""PLEI API — FastAPI router under /plei.

Endpoints:
    GET  /plei/understand   → project summary
    GET  /plei/status       → lifecycle + health
    GET  /plei/lifecycle    → detailed lifecycle classification
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


@plei_router.get("/calibrate", summary="Calibration report — error metrics, reliability, schedule, feedback")
async def project_calibrate():
    """Run the full Phase 7 calibration pipeline.

    Reads the calibration store (.plei/calibration.jsonl), computes
    error metrics (MAPE, Brier, ECE), builds the reliability diagram,
    checks the scheduler, and produces feedback adjustments.
    """
    from msb_v3.plei.calibration.error import (
        compute_error_metrics,
        error_metrics_as_dict,
    )
    from msb_v3.plei.calibration.feedback import adjustment_as_dict, compute_adjustments
    from msb_v3.plei.calibration.reliability import (
        build_reliability_diagram,
        reliability_as_dict,
    )
    from msb_v3.plei.calibration.scheduler import compute_schedule, schedule_as_dict
    from msb_v3.plei.calibration.store import CalibrationStore

    store = CalibrationStore()
    pairs = store.pairs()
    metrics = compute_error_metrics(pairs)
    reliability = build_reliability_diagram(pairs)
    schedule = compute_schedule(store)
    adj = compute_adjustments(metrics)

    return {
        "total_predictions": store.prediction_count(),
        "total_outcomes": store.outcome_count(),
        "total_pairs": len(pairs),
        "chain_integrity": _check_chain(store),
        "error": error_metrics_as_dict(metrics),
        "reliability": reliability_as_dict(reliability),
        "schedule": schedule_as_dict(schedule),
        "feedback": adjustment_as_dict(adj),
    }


@plei_router.get("/reliability", summary="Reliability diagram — per-bucket calibration accuracy")
async def project_reliability():
    """5-bucket reliability diagram with drift detection."""
    from msb_v3.plei.calibration.reliability import (
        build_reliability_diagram,
        reliability_as_dict,
    )
    from msb_v3.plei.calibration.store import CalibrationStore

    store = CalibrationStore()
    diagram = build_reliability_diagram(store.pairs())
    return reliability_as_dict(diagram)


@plei_router.post("/calibrate/outcome", summary="Record a calibration outcome and trigger re-calibration")
async def record_outcome(
    prediction_id: str = Query(..., description="Prediction ID to match"),
    actual_duration_days: float = Query(..., description="Actual duration in days"),
    failures_encountered: int = Query(default=0, description="How many failure events fired"),
    actual_stage: str = Query(default="", description="Current lifecycle stage"),
    note: str = Query(default="", description="Context note"),
):
    """Record an observed outcome and pair it with a prediction.

    This closes the calibration loop — prediction → outcome → error.
    Automatically triggers re-calibration if threshold met.
    """
    import time
    import uuid

    from msb_v3.plei.calibration.store import CalibrationStore, Outcome

    store = CalibrationStore()
    outcome = Outcome(
        outcome_id=f"outcome:{uuid.uuid4().hex[:12]}",
        prediction_id=prediction_id,
        project="msb-v3",
        observed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        actual_duration_days=actual_duration_days,
        actual_completion=True,
        failures_encountered=failures_encountered,
        severity="critical" if failures_encountered >= 3 else "major" if failures_encountered >= 1 else "none",
        actual_stage=actual_stage,
        error_note=note,
    )
    store.record_outcome(outcome)

    return {
        "recorded": True,
        "outcome_id": outcome.outcome_id,
        "prediction_id": prediction_id,
    }


def _check_chain(store: Any) -> dict[str, Any]:
    """Check chain integrity, graceful."""
    try:
        ok, msg = store.verify_chain()
        return {"ok": ok, "message": msg}
    except Exception:
        return {"ok": False, "message": "could not verify chain"}


@plei_router.post("/execute", summary="Execute the top PLEI recommendation through governed harness")
async def plei_execute(
    project_root: str = Query(default=_DEFAULT_ROOT, description="Project root path"),
    session: str = Query(default="plei-default", description="Execution session ID"),
):
    """Run PLEI's top recommendation through the governed harness bridge.

    This is the Phase 6 endpoint — it:
    1. Runs the full PLEI analysis (ingest + twin + lifecycle + decide)
    2. Converts the top NextAction into a WorkPlan
    3. Gates every step through the ActionGate
    4. Executes through the 10-provider seam with fallback chain
    5. Verifies claims through MoIE
    6. Logs evidence into the spine
    7. Closes the evidence loop — updates twin, re-classifies lifecycle

    Returns the complete ExecutionReport + LoopResult.
    """

    from msb_v3.plei.decisions.next_action import (
        select_next_action,
    )
    from msb_v3.plei.decisions.prioritization import prioritize
    from msb_v3.plei.decisions.provider_selection import (
        build_profiles,
        select_provider_for_task,
    )
    from msb_v3.plei.engineering.gap_detector import detect_gaps, gap_report_as_dict
    from msb_v3.plei.harness.bridge import (
        execute_plan,
        execution_report_as_dict,
    )
    from msb_v3.plei.harness.evidence_loop import (
        loop_result_as_dict,
        run_evidence_loop,
    )
    from msb_v3.plei.harness.work_plan import build_work_plan
    from msb_v3.plei.risk.report import analyze_risk, risk_report_as_dict

    try:
        root = Path(project_root).resolve()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid project_root: {e}")

    # Step 1: Full PLEI analysis
    twin = ingest_all(root)
    lc = classify_lifecycle(twin)
    gaps = detect_gaps(twin)
    risk = analyze_risk(twin)
    gap_dict = gap_report_as_dict(gaps)
    risk_dict = risk_report_as_dict(risk)

    # Step 2: Decision pipeline
    prio = prioritize(gap_dict, risk_dict)
    profiles = build_profiles()
    prov_avail = {p.provider_id: p.available for p in profiles}
    next_report = select_next_action(prio, prov_avail)

    top_na = next_report.primary if next_report else None
    if not top_na or not top_na.action:
        return {"ok": False, "error": "no actionable recommendation found"}

    # Build provider selection
    provider_sel = select_provider_for_task(
        task_description=top_na.action.description,
        required_capabilities=top_na.action.recommended_providers,
        max_risk_tier=4,
        profiles=profiles,
    )

    # Step 3: Build WorkPlan
    na_dict = {
        "action_id": top_na.action.action_id,
        "description": top_na.action.description,
        "category": top_na.action.category,
        "score": top_na.action.score,
        "expected_outcome": top_na.expected_outcome,
        "validation_checks": top_na.validation_checks,
    }
    prov_dict = None
    if provider_sel:
        try:
            prov_dict = {
                "primary": {"provider_id": provider_sel.primary.provider_id} if provider_sel.primary else None,
                "fallbacks": [{"provider_id": f.provider_id} for f in provider_sel.fallbacks],
                "rationale": provider_sel.rationale,
            }
        except Exception:
            prov_dict = None

    plan = build_work_plan(na_dict, prov_dict)

    # Step 4: Setup governed bridge
    providers_by_id: dict[str, Any] = {}
    for p in profiles:
        if hasattr(p, "_provider"):
            providers_by_id[p.provider_id] = p._provider  # type: ignore[attr-defined]

    # Fall back to real provider registry if profiles lack _provider handles
    if not providers_by_id:
        try:
            from msb_v3.agent.providers import ProviderRegistry

            reg = ProviderRegistry()
            real_map: dict[str, Any] = {}
            for ap in reg.select():
                if hasattr(ap, "spec"):
                    real_map[ap.spec.provider_id] = ap
            providers_by_id = real_map
        except Exception:
            pass

    # ActionGate
    gate = None
    try:
        from msb_v3.agent.safety import ActionGate
        gate = ActionGate()
    except Exception:
        pass

    # MoIE
    moie = None
    try:
        from msb_v3.moie.engine import MoIEController
        moie = MoIEController()
    except Exception:
        pass

    # Evidence spine
    spine = None
    try:
        from msb_v3.evidence.spine import DecisionEvidenceStore
        spine = DecisionEvidenceStore()
    except Exception:
        pass

    # Step 5: Execute through harness bridge
    exec_report: Any = await execute_plan(
        plan,
        providers_by_id=providers_by_id,
        gate=gate,
        moie=moie,
        evidence_spine=spine,
        approved_capabilities=set(),
        session=session,
    )

    # Step 6: Evidence loop — close the feedback loop
    prev_stage = lc.stage.value if hasattr(lc.stage, "value") else str(lc.stage)
    loop_result = run_evidence_loop(
        exec_report,
        twin,
        previous_stage=prev_stage,
        previous_confidence=lc.confidence,
    )

    return {
        "ok": exec_report.ok,
        "execution": execution_report_as_dict(exec_report),
        "evidence_loop": loop_result_as_dict(loop_result),
    }
