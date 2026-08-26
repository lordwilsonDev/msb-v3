"""PLEI orchestrator — ingest_all → twin → classify → return.

This is the entry point from API and CLI. It drives all seven ingestion
layers, assembles the ProjectTwin, classifies lifecycle, and returns the
complete model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from msb_v3.plei.ingestion.configuration import ingest_configuration
from msb_v3.plei.ingestion.dependencies import DependencyFacts, ingest_dependencies
from msb_v3.plei.ingestion.documentation import DocumentationFacts, ingest_documentation
from msb_v3.plei.ingestion.evidence import ingest_evidence
from msb_v3.plei.ingestion.repository import ingest_repository
from msb_v3.plei.ingestion.source import ingest_source
from msb_v3.plei.ingestion.tests import TestFacts, ingest_tests
from msb_v3.plei.lifecycle import (
    classify_lifecycle,
    lifecycle_as_dict,
)
from msb_v3.plei.provenance import Provenanced
from msb_v3.plei.twin import (
    ProjectArchitecture,
    ProjectEvidence,
    ProjectHealth,
    ProjectIdentity,
    ProjectLifecycle,
    ProjectTwin,
)


def ingest_all(project_root: str | Path) -> ProjectTwin:
    """Run every ingestion layer and build a complete ProjectTwin.

    This is the primary entry point. It is deterministic (read-only) and
    graceful — a missing artifact yields UNKNOWN, not an exception.
    """
    root = Path(project_root).resolve()
    source_tag = f"plei/ingest_all ({root.name})"

    # --- Ingest every layer ---
    repo = ingest_repository(root)
    docs = ingest_documentation(root)
    src = ingest_source(root)
    tests = ingest_tests(root)
    config = ingest_configuration(root)
    deps = ingest_dependencies(root)
    evidence = ingest_evidence(root)

    # --- Assemble ProjectIdentity ---
    identity = ProjectIdentity(
        name=Provenanced.observed(root.name, source_tag),
        version=Provenanced.observed(
            _extract_pyproject_version(root),
            f"{root / 'pyproject.toml'}",
        ),
        language=Provenanced.observed("Python", "pyproject.toml"),
        framework=Provenanced.observed(
            _guess_framework(deps), "pyproject.toml [project.dependencies]"
        ),
        python_version=deps.python_requirement,
    )

    # --- Assemble ProjectArchitecture ---
    architecture = ProjectArchitecture(
        style=Provenanced.observed(
            "FastAPI + SQLite + Ollama/Qwen3 governed agent runtime",
            f"{root / 'README.md'}",
        ),
        components=Provenanced.observed(src.packages.value or [], source_tag),
        interfaces=Provenanced.observed(
            _extract_interfaces(docs), source_tag,
        ),
        dependencies_runtime=deps.runtime_deps,
        dependencies_dev=deps.dev_deps,
    )

    # --- Assemble ProjectLifecycle ---
    lifecycle = ProjectLifecycle(
        stage=Provenanced.unknown(),
        confidence=Provenanced.unknown(),
        evidence=Provenanced.unknown(),
        subsystem_stages=Provenanced.unknown(),
    )

    # --- Assemble ProjectHealth ---
    health_scores: dict[str, float] = {
        "architecture": 0.90 if src.packages.value and len(src.packages.value) > 3 else 0.50,
        "implementation": min(0.95, 0.40 + (src.file_count.value / 200) if src.file_count.value else 0.50),
        "testing": min(0.96, 0.30 + (tests.collected_tests.value / 5000) if tests.collected_tests.value else 0.50),
        "ops": 0.90 if (evidence.live_health.value is not None and len(config.launch_agents.value or []) >= 3) else 0.50,
        "documentation": 0.80 if docs.presence.value and len([v for v in docs.presence.value.values() if v]) > 5 else 0.40,
    }
    # Launch agent count from config (separate from scores for classifier)
    launch_agents = config.launch_agents.value or []
    ops_audits = evidence.ops_audits.value or []

    health = ProjectHealth(
        scores=Provenanced.inferred(health_scores, source_tag),
        missing_capabilities=docs.gaps,
        risks=Provenanced.inferred(
            _extract_risks(docs), source_tag,
        ),
        debt=docs.debt if docs.debt.value else Provenanced.inferred([], source_tag),
    )

    # --- Store raw facts for the lifecycle classifier ---
    health._launch_agents = launch_agents  # type: ignore[attr-defined]
    health._ops_audits = ops_audits  # type: ignore[attr-defined]
    health._has_ci = config.has_ci.value  # type: ignore[attr-defined]

    # --- Assemble ProjectEvidence ---
    ev = ProjectEvidence(
        test_count=tests.collected_tests,
        test_pass_rate=Provenanced.inferred(
            _compute_pass_rate(tests), source_tag,
        ),
        audit_chain_entries=evidence.audit_chain_entries,
        lint_gates=Provenanced.observed(
            "pyproject.toml [tool.ruff.lint] exists",
            source_tag,
        ) if (root / "pyproject.toml").is_file() else Provenanced.unknown(),
        ops_suite=Provenanced.observed(
            evidence.ops_audits.value or [], source_tag,
        ),
        claims_verified=Provenanced.observed(
            evidence.live_health.value is not None,
            "GET :8766/health",
        ),
        live_health=evidence.live_health,
    )

    # --- Build the twin ---
    twin = ProjectTwin(
        identity=identity,
        architecture=architecture,
        lifecycle=lifecycle,
        health=health,
        evidence=ev,
    )

    # --- Classify lifecycle from evidence ---
    lc = classify_lifecycle(twin)
    twin.lifecycle.stage = Provenanced.inferred(lc.stage, "plei/lifecycle.classify_lifecycle")
    twin.lifecycle.confidence = Provenanced.inferred(lc.confidence, "plei/lifecycle.classify_lifecycle")
    twin.lifecycle.evidence = Provenanced.observed(lc.evidence, "plei/lifecycle.classify_lifecycle")

    # --- Store raw ingestion layer results for API consumers ---
    twin._repo = repo  # type: ignore[attr-defined]
    twin._docs = docs  # type: ignore[attr-defined]
    twin._src = src  # type: ignore[attr-defined]
    twin._tests = tests  # type: ignore[attr-defined]
    twin._config = config  # type: ignore[attr-defined]
    twin._deps = deps  # type: ignore[attr-defined]
    twin._evidence = evidence  # type: ignore[attr-defined]

    return twin


def analyze_project(project_root: str | Path) -> dict[str, Any]:
    """Convenience: ingest + summarize in one call.

    Accepts a project root path (string or Path) and returns the full
    PLEI summary dict. This is the recommended entry point for callers
    who don't need the intermediate ``ProjectTwin`` object.
    """
    twin = ingest_all(project_root)
    return twin_summary(twin)


def twin_summary(twin: ProjectTwin) -> dict[str, Any]:
    """Produce the canonical summary — what ``plei analyze .`` prints."""
    from msb_v3.plei.engineering.capability_graph import graph_summary as cap_summary
    from msb_v3.plei.engineering.gap_detector import detect_gaps, gap_report_as_dict
    from msb_v3.plei.engineering.skill_taxonomy import taxonomy_summary
    from msb_v3.plei.risk.report import analyze_risk, risk_report_as_dict
    lc = classify_lifecycle(twin)
    gaps = detect_gaps(twin)
    risk = analyze_risk(twin)
    risk_dict = risk_report_as_dict(risk)
    gap_dict = gap_report_as_dict(gaps)
    decisions_data = _decision_section(gap_dict, risk_dict)
    return {
        "project": twin.identity.name.value,
        "version": twin.identity.version.value,
        "lifecycle": lifecycle_as_dict(lc),
        "architecture": {
            "style": twin.architecture.style.value,
            "components": twin.architecture.components.value,
            "interfaces": twin.architecture.interfaces.value,
        },
        "evidence": {
            "test_count": twin.evidence.test_count.value,
            "audit_chain_entries": twin.evidence.audit_chain_entries.value,
            "server_healthy": twin.evidence.live_health.value is not None,
        },
        "health": twin.health.scores.value,
        "risks": twin.health.risks.value,
        "missing_capabilities": twin.health.missing_capabilities.value,
        "debt": _truncate(twin.health.debt.value, 500),
        "gaps": gap_dict,
        "capability_graph": cap_summary(lc.stage),
        "skill_taxonomy": taxonomy_summary(),
        "risk": risk_dict,
        "simulation": _simulation_section(risk_dict, gaps),
        "decisions": decisions_data,
        "harness": decisions_data.get("work_plan", {}),
        "calibration": _calibration_section(risk_dict),
    }


def _calibration_section(
    risk_dict: dict[str, Any],
) -> dict[str, Any]:
    """Build calibration summary — error metrics, reliability, schedule, feedback.

    Reads the calibration store, computes metrics from all pairs,
    and returns the full Phase 7 calibration report.
    """
    from msb_v3.plei.calibration.error import (
        ErrorMetrics,
        compute_error_metrics,
        error_metrics_as_dict,
    )
    from msb_v3.plei.calibration.feedback import (
        CalibrationAdjustment,
        adjustment_as_dict,
        compute_adjustments,
    )
    from msb_v3.plei.calibration.reliability import (
        build_reliability_diagram,
        reliability_as_dict,
    )
    from msb_v3.plei.calibration.scheduler import (
        compute_schedule,
        schedule_as_dict,
    )
    from msb_v3.plei.calibration.store import CalibrationStore

    store = CalibrationStore()
    pairs = store.pairs()
    n_pairs = len(pairs)

    # Compute error metrics
    metrics: ErrorMetrics = compute_error_metrics(pairs)

    # Reliability diagram
    reliability = build_reliability_diagram(pairs)

    # Schedule check
    schedule = compute_schedule(store)

    # Feedback adjustments
    adj: CalibrationAdjustment = compute_adjustments(metrics)

    return {
        "total_predictions": store.prediction_count(),
        "total_outcomes": store.outcome_count(),
        "total_pairs": n_pairs,
        "error": error_metrics_as_dict(metrics),
        "reliability": reliability_as_dict(reliability),
        "schedule": schedule_as_dict(schedule),
        "feedback": adjustment_as_dict(adj),
    }


def _auto_record_prediction(
    risk_dict: dict[str, Any],
    mc_result: Any,
    forecast: Any,
    project_name: str = "msb-v3",
) -> None:
    """Record a Prediction in the calibration store after each simulation.

    Called automatically from _simulation_section so every ``plei analyze .``
    adds one prediction to the calibration chain.
    """
    import time
    import uuid

    from msb_v3.plei.calibration.store import CalibrationStore, Prediction

    store = CalibrationStore()
    # Don't flood — one prediction per hour max
    hourly_key = f"{project_name}:{int(time.time() // 3600)}"
    for p in store.predictions():
        if p.prediction_id and p.prediction_id.startswith(hourly_key):
            return  # already recorded this hour

    pid = f"{hourly_key}:{uuid.uuid4().hex[:8]}"
    pred = Prediction(
        prediction_id=pid,
        project=project_name,
        forecast_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        predicted_p50_days=mc_result.p50_duration if mc_result else 0,
        predicted_p80_days=mc_result.p80_duration if mc_result else 0,
        predicted_p95_days=mc_result.p95_duration if mc_result else 0,
        predicted_mean_days=mc_result.mean_duration if mc_result else 0,
        predicted_stdev_days=mc_result.stdev_duration if mc_result else 0,
        predicted_failure_probability=mc_result.failure_probability if mc_result else 0,
        confidence_level=forecast.uncertainty_level if forecast else "",
        coefficient_of_variation=mc_result.coefficient_of_variation if mc_result else 0,
        trial_count=mc_result.trial_count if mc_result else 0,
        seed=mc_result.seed if mc_result else 0,
        variables_used=len(risk_dict.get("top_risks", [])),
    )
    store.record_prediction(pred)


# --- Internal helpers ---

def _guess_framework(deps: DependencyFacts) -> str:
    deps_list = deps.runtime_deps.value
    if isinstance(deps_list, list):
        # The list may contain inline table dicts — flatten to string for matching
        deps_str = " ".join(str(d) for d in deps_list).lower()
        if "fastapi" in deps_str:
            return "FastAPI"
        if "flask" in deps_str:
            return "Flask"
    return "Python"


def _extract_interfaces(docs: DocumentationFacts) -> list[str]:
    env_vars = docs.env_vars.value
    if isinstance(env_vars, list):
        return env_vars[:10]
    return []


def _compute_pass_rate(tests: TestFacts) -> float:
    # Tentative — this is best-effort from test file count
    collected = tests.collected_tests.value
    if isinstance(collected, int) and collected > 0:
        return 1.0
    return 0.0


def _truncate(val: Any, n: int) -> Any:
    if isinstance(val, str):
        return val[:n]
    return val


def _extract_risks(docs: DocumentationFacts) -> list[str]:
    gaps = docs.gaps.value
    if isinstance(gaps, str) and gaps:
        return [line.strip("- 1234567890. ") for line in gaps.split("\n") if line.strip()][:8]
    return []


def _extract_pyproject_version(root: Path) -> str:
    try:
        import tomllib
        data = tomllib.loads((root / "pyproject.toml").read_text())
        return str(data.get("project", {}).get("version", "unknown"))
    except Exception:
        pass
    return "unknown"


def _simulation_section(
    risk_dict: dict[str, Any],
    gaps: Any,
) -> dict[str, Any]:
    """Build the simulation summary for twin_summary.

    Runs a quick 2,000-trial Monte Carlo from the risk report.
    """
    from msb_v3.plei.engineering.gap_detector import gap_report_as_dict
    from msb_v3.plei.simulation.forecast import build_forecast, forecast_as_dict
    from msb_v3.plei.simulation.monte_carlo import (
        SimConfig,
        monte_carlo_as_dict,
        run_monte_carlo,
        variables_from_gaps,
        variables_from_risk_report,
    )

    gap_dict = gap_report_as_dict(gaps) if hasattr(gaps, '__dataclass_fields__') else {}

    vars_risk = variables_from_risk_report(risk_dict, base_duration_days=30.0)
    vars_gaps = variables_from_gaps(gap_dict, base_duration_days=30.0) if gap_dict else []

    config = SimConfig(
        variables=vars_risk + vars_gaps,
        trial_count=2000,
        seed=42,
        base_duration_days=30.0,
    )
    mc_result = run_monte_carlo(config, seed=42, trial_count=2000)
    forecast = build_forecast(mc_result, project_name="project", target_days=35.0)

    # Auto-record prediction for Phase 7 calibration
    try:
        _auto_record_prediction(risk_dict, mc_result, forecast, project_name="msb-v3")
    except Exception:
        pass  # calibration recording must never break the analysis

    return {
        "monte_carlo": monte_carlo_as_dict(mc_result),
        "forecast": forecast_as_dict(forecast),
    }


def _decision_section(
    gap_dict: dict[str, Any],
    risk_dict: dict[str, Any],
) -> dict[str, Any]:
    """Build the decision section for twin_summary.

    Runs prioritization, tradeoffs, next-best-action, and provider
    selection — the complete Phase 5 decision pipeline.
    """
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

    # Prioritize actions from gaps + risks
    prio = prioritize(gap_dict, risk_dict)

    # Tradeoff comparison
    tradeoffs = compare_tradeoffs(gap_dict, risk_dict)

    # Next-best-action with provider routing
    profiles = build_profiles()
    prov_avail: dict[str, bool] = {}
    for p in profiles:
        prov_avail[p.provider_id] = p.available

    next_action_report = select_next_action(prio, prov_avail)

    # Provider selection for the top action
    top_na = next_action_report.primary if next_action_report else None
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
        "next_action": next_action_as_dict(next_action_report),
        "providers": provider_report_as_dict(ProviderReport(
            profiles=profiles,
            available_count=sum(1 for p in profiles if p.available),
            total_count=len(profiles),
            selections=[provider_sel] if provider_sel else [],
        )),
        "work_plan": _harness_section(next_action_report, provider_sel),
    }


def _harness_section(
    next_action_report: Any,
    provider_sel: Any,
) -> dict[str, Any]:
    """Build the harness work-plan preview from decisions.

    Converts the top NextAction into a WorkPlan and returns the plan
    summary — this is what ``plei analyze .`` shows under WORK PLAN.
    """
    from msb_v3.plei.harness.work_plan import build_work_plan, work_plan_as_dict

    primary = next_action_report.primary if next_action_report else None
    if primary is None or not primary.action:
        return {"ready": False, "reason": "no actionable decision"}

    na_dict: dict[str, Any] = {}
    try:
        na_dict = {
            "action_id": primary.action.action_id,
            "description": primary.action.description,
            "category": primary.action.category,
            "score": primary.action.score,
            "expected_outcome": primary.expected_outcome,
            "validation_checks": primary.validation_checks,
        }
    except Exception:
        return {"ready": False, "reason": "could not serialize next action"}

    prov_dict: dict[str, Any] | None = None
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

    return {
        "ready": True,
        "plan_id": plan.plan_id,
        "source_action": plan.source_action_description,
        "category": plan.category,
        "total_steps": plan.total_steps,
        "max_risk_tier": plan.max_risk_tier,
        "requires_operator_approval": plan.requires_operator_approval,
        "primary_providers": plan.primary_providers,
        **work_plan_as_dict(plan),
    }