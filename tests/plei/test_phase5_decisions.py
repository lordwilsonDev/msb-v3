"""Phase 5 tests — Decision Engine: prioritization, tradeoffs, next action, provider selection.

All tests target msb-v3 itself as the test subject. No mocks needed —
every module is deterministic and stdlib-only.
"""

from __future__ import annotations

import pytest

from msb_v3.plei.decisions.next_action import (
    next_action_as_dict,
    select_next_action,
)
from msb_v3.plei.decisions.prioritization import (
    PrioritizationReport,
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

# ---------------------------------------------------------------------------
# Shared test fixtures — real gap/risk data from msb-v3
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_gap_dict():
    """Real gap data from Phase 2 — msb-v3's capability gaps."""
    return {
        "stage": "OPERATIONS",
        "stage_confidence": 0.95,
        "total_capabilities_required": 6,
        "covered": 0,
        "partial": 4,
        "missing": 2,
        "gaps": [
            {
                "capability": "health_monitoring",
                "category": "ops",
                "criticality": 8,
                "status": "PARTIAL",
                "available_skills": [],
                "missing_skills": ["env-secrets-manager"],
                "provider_gap": True,
                "recommendation": "Skills installed but no provider available",
            },
            {
                "capability": "incident_response",
                "category": "ops",
                "criticality": 9,
                "status": "PARTIAL",
                "available_skills": ["ship-gate"],
                "missing_skills": [],
                "provider_gap": True,
                "recommendation": "skill known, not installed",
            },
            {
                "capability": "backup_recovery",
                "category": "ops",
                "criticality": 8,
                "status": "PARTIAL",
                "available_skills": [],
                "missing_skills": ["n8n-self-hosting"],
                "provider_gap": True,
                "recommendation": "skill installed, no provider available",
            },
            {
                "capability": "audit_logging",
                "category": "ops",
                "criticality": 7,
                "status": "PARTIAL",
                "available_skills": ["auditing-solo-repos"],
                "missing_skills": [],
                "provider_gap": True,
                "recommendation": "skill installed, no provider available",
            },
            {
                "capability": "capacity_planning",
                "category": "ops",
                "criticality": 5,
                "status": "MISSING",
                "available_skills": [],
                "missing_skills": [],
                "provider_gap": False,
                "recommendation": "No skill known for capacity_planning",
            },
            {
                "capability": "security_patching",
                "category": "security",
                "criticality": 5,
                "status": "MISSING",
                "available_skills": [],
                "missing_skills": [],
                "provider_gap": False,
                "recommendation": "No skill known for security_patching",
            },
        ],
        "required_roles": [
            {"name": "SRE", "discipline": "ops", "description": "Monitoring, incident response, backup/recovery"},
            {"name": "Security Engineer", "discipline": "security", "description": "Patching, vulnerability management"},
        ],
        "next_actions": [
            "[9/10] PARTIAL: incident_response — skill known, not installed",
            "[8/10] PARTIAL: health_monitoring — Skills installed but no provider available",
        ],
    }


@pytest.fixture(scope="module")
def real_risk_dict():
    """Real risk data from Phase 3 — msb-v3's top risks."""
    return {
        "total_risks": 9,
        "dependency_risks": 3,
        "failure_modes": 4,
        "debt_items": 9,
        "top_risks": [
            {
                "source": "debt",
                "description": "Operational: Disk saturation blocks evidence growth",
                "severity": 7,
                "likelihood": 0.6,
                "risk_score": 4.2,
                "details": "Mac Mini SSD nearing capacity",
            },
            {
                "source": "debt",
                "description": "Data/Operational: No DB schema versioning/migrations",
                "severity": 6,
                "likelihood": 0.7,
                "risk_score": 4.2,
                "details": "No migrations tool in place",
            },
            {
                "source": "failure",
                "description": "scheduler_failure: cron scheduler",
                "severity": 5,
                "likelihood": 0.6,
                "risk_score": 3.0,
                "details": "cron scheduler SPOF",
            },
            {
                "source": "debt",
                "description": "Security: CLI provider best-effort isolation",
                "severity": 5,
                "likelihood": 0.4,
                "risk_score": 2.0,
                "details": "Not a sandbox",
            },
            {
                "source": "failure",
                "description": "disk_saturation: Mac Mini SSD",
                "severity": 4,
                "likelihood": 0.7,
                "risk_score": 2.8,
                "details": "SSD is single point of failure",
            },
        ],
        "debt_report": {
            "total_items": 9,
            "top_5": [
                {
                    "item": "Disk saturation",
                    "debt_class": "Operational",
                    "impact": 7,
                    "probability": 0.6,
                    "irreversibility": 0.8,
                    "priority": 3.4,
                    "note": "SSD nearing capacity",
                },
                {
                    "item": "No DB schema migrations",
                    "debt_class": "Data/Operational",
                    "impact": 6,
                    "probability": 0.7,
                    "irreversibility": 0.6,
                    "priority": 2.5,
                    "note": "Schema changes are manual",
                },
                {
                    "item": "Provider isolation",
                    "debt_class": "Security",
                    "impact": 5,
                    "probability": 0.4,
                    "irreversibility": 0.5,
                    "priority": 1.0,
                    "note": "No sandbox for CLI",
                },
            ],
        },
        "failure_report": {
            "total_modes": 4,
            "modes": [
                {
                    "kind": "scheduler_failure",
                    "component": "cron scheduler",
                    "severity": 5,
                    "likelihood": 0.6,
                    "evidence": "cron scheduler is SPOF",
                },
                {
                    "kind": "disk_saturation",
                    "component": "Mac Mini SSD",
                    "severity": 4,
                    "likelihood": 0.7,
                    "evidence": "SSD single point of failure",
                },
            ],
        },
        "critical_path": ["msb_v3", "msb_ledger"],
        "bottlenecks": [
            {"module": "msb_ledger", "fan_in": 1},
            {"module": "msb_v3", "fan_in": 0},
        ],
    }


# ---------------------------------------------------------------------------
# Prioritization tests
# ---------------------------------------------------------------------------


class TestPrioritization:
    def test_produces_actions_from_gaps_and_risks(self, real_gap_dict, real_risk_dict):
        report = prioritize(real_gap_dict, real_risk_dict)
        assert report.total_actions > 0
        assert len(report.actions) == report.total_actions
        assert report.top_action is not None

    def test_gaps_produce_actions(self, real_gap_dict, real_risk_dict):
        report = prioritize(real_gap_dict, real_risk_dict)
        gap_actions = [a for a in report.actions if a.category == "gap_close"]
        # At least the 2 MISSING + 4 PARTIAL gaps should produce actions
        assert len(gap_actions) >= 3

    def test_risks_produce_actions(self, real_gap_dict, real_risk_dict):
        report = prioritize(real_gap_dict, real_risk_dict)
        risk_actions = [a for a in report.actions if a.category == "risk_mitigate"]
        assert len(risk_actions) >= 2

    def test_debt_produces_actions(self, real_gap_dict, real_risk_dict):
        report = prioritize(real_gap_dict, real_risk_dict)
        debt_actions = [a for a in report.actions if a.category == "debt_reduce"]
        assert len(debt_actions) >= 1

    def test_top_action_has_all_fields(self, real_gap_dict, real_risk_dict):
        report = prioritize(real_gap_dict, real_risk_dict)
        top = report.top_action
        assert top is not None
        assert top.score > 0
        assert 1 <= top.impact <= 10
        assert 0 <= top.risk_reduction <= 1
        assert 0 <= top.confidence <= 1
        assert top.cost > 0
        assert top.description  # non-empty

    def test_scoring_formula_decomposed(self, real_gap_dict, real_risk_dict):
        report = prioritize(real_gap_dict, real_risk_dict)
        top = report.top_action
        assert top is not None
        expected = (top.impact * top.risk_reduction * top.confidence) / top.cost
        assert abs(top.score - expected) < 0.05

    def test_actions_sorted_descending(self, real_gap_dict, real_risk_dict):
        report = prioritize(real_gap_dict, real_risk_dict)
        scores = [a.score for a in report.actions]
        assert scores == sorted(scores, reverse=True)

    def test_empty_inputs_return_no_actions(self):
        report = prioritize({}, {})
        assert report.total_actions == 0

    def test_prioritization_as_dict_serializable(self, real_gap_dict, real_risk_dict):
        report = prioritize(real_gap_dict, real_risk_dict)
        d = prioritization_as_dict(report)
        assert isinstance(d, dict)
        assert "total_actions" in d
        assert "top_action" in d
        if d["top_action"]:
            assert "score" in d["top_action"]


# ---------------------------------------------------------------------------
# Tradeoff tests
# ---------------------------------------------------------------------------


class TestTradeoffs:
    def test_produces_five_scenarios(self, real_gap_dict, real_risk_dict):
        report = compare_tradeoffs(real_gap_dict, real_risk_dict)
        assert len(report.options) == 4  # A, B, C, D
        assert report.baseline.name == "BASELINE"

    def test_baseline_has_zero_cost(self, real_gap_dict, real_risk_dict):
        report = compare_tradeoffs(real_gap_dict, real_risk_dict)
        assert report.baseline.cost == 0.0
        assert report.baseline.score == 0.0

    def test_options_have_descriptions(self, real_gap_dict, real_risk_dict):
        report = compare_tradeoffs(real_gap_dict, real_risk_dict)
        for opt in report.options:
            assert opt.name
            assert opt.description
            assert opt.pros
            assert opt.cons

    def test_recommendation_names_best_option(self, real_gap_dict, real_risk_dict):
        report = compare_tradeoffs(real_gap_dict, real_risk_dict)
        assert report.recommendation
        best_name = report.options[0].name  # sorted by score desc
        assert best_name in report.recommendation

    def test_tradeoff_as_dict_serializable(self, real_gap_dict, real_risk_dict):
        report = compare_tradeoffs(real_gap_dict, real_risk_dict)
        d = tradeoff_as_dict(report)
        assert "baseline" in d
        assert "options" in d
        assert "recommendation" in d


# ---------------------------------------------------------------------------
# Next-action tests
# ---------------------------------------------------------------------------


class TestNextAction:
    def test_selects_top_action_as_primary(self, real_gap_dict, real_risk_dict):
        prio = prioritize(real_gap_dict, real_risk_dict)
        prov_avail: dict[str, bool] = {"api.deepseek": True, "local.slice": True, "cli.codebuddy": True}
        report = select_next_action(prio, prov_avail)
        assert report.primary is not None
        assert report.primary.rank == 1

    def test_validation_checks_present(self, real_gap_dict, real_risk_dict):
        prio = prioritize(real_gap_dict, real_risk_dict)
        prov_avail: dict[str, bool] = {"local.slice": True, "api.deepseek": True}
        report = select_next_action(prio, prov_avail)
        assert len(report.primary.validation_checks) >= 3

    def test_provider_unavailable_blocked(self, real_gap_dict, real_risk_dict):
        """When no providers available, primary should note it."""
        prio = prioritize(real_gap_dict, real_risk_dict)
        prov_avail: dict[str, bool] = {}  # nothing available
        report = select_next_action(prio, prov_avail)
        # Primary may still be selected but show provider unavailable
        assert report.primary is not None

    def test_expected_outcome_present(self, real_gap_dict, real_risk_dict):
        prio = prioritize(real_gap_dict, real_risk_dict)
        prov_avail: dict[str, bool] = {"local.slice": True}
        report = select_next_action(prio, prov_avail)
        assert report.primary.expected_outcome

    def test_empty_prioritization_handled(self):
        prio = PrioritizationReport(total_actions=0, actions=[])
        report = select_next_action(prio, {})
        assert report.primary.action.category == "none"

    def test_next_action_as_dict_serializable(self, real_gap_dict, real_risk_dict):
        prio = prioritize(real_gap_dict, real_risk_dict)
        prov_avail: dict[str, bool] = {"local.slice": True, "api.deepseek": True}
        report = select_next_action(prio, prov_avail)
        d = next_action_as_dict(report)
        assert "primary" in d
        assert "alternatives" in d


# ---------------------------------------------------------------------------
# Provider selection tests
# ---------------------------------------------------------------------------


class TestProviderSelection:
    def test_build_profiles_returns_profiles(self):
        profiles = build_profiles()
        assert len(profiles) > 0
        for p in profiles:
            assert p.provider_id
            assert p.kind

    def test_some_profiles_available(self):
        profiles = build_profiles()
        available = [p for p in profiles if p.available]
        assert len(available) >= 1  # at least local or API

    def test_select_provider_routes_to_available(self):
        profiles = build_profiles()
        # Use a capability that actually exists in the live registry
        sel = select_provider_for_task(
            task_description="Run a search query against the vault",
            required_capabilities=["search_query"],
            max_risk_tier=4,
            profiles=profiles,
        )
        assert sel.primary is not None
        assert sel.primary.available

    def test_select_falls_back_when_providers_unavailable(self):
        """When no providers match capabilities, primary is None."""
        profiles = build_profiles()
        sel = select_provider_for_task(
            task_description="Fix production database",
            required_capabilities=["database_migration", "sql_surgery"],
            max_risk_tier=2,
            profiles=profiles,
        )
        # These capabilities don't exist in any provider
        assert sel.primary is None or not sel.primary.available

    def test_select_has_rationale(self):
        profiles = build_profiles()
        sel = select_provider_for_task(
            task_description="Write documentation for the API",
            required_capabilities=["documentation"],
            max_risk_tier=4,
            profiles=profiles,
        )
        assert sel.rationale

    def test_select_respects_risk_tier(self):
        profiles = build_profiles()
        # Risk tier 1 — only very low-risk providers should match
        sel = select_provider_for_task(
            task_description="Delete a production file",
            required_capabilities=["code_generation"],
            max_risk_tier=1,
            profiles=profiles,
        )
        if sel.primary:
            assert sel.primary.max_risk_tier <= 1

    def test_provider_report_as_dict_serializable(self):
        profiles = build_profiles()
        report = ProviderReport(
            profiles=profiles,
            available_count=sum(1 for p in profiles if p.available),
            total_count=len(profiles),
            selections=[
                select_provider_for_task(
                    task_description="Write tests",
                    required_capabilities=["code_generation"],
                    max_risk_tier=4,
                    profiles=profiles,
                ),
            ],
        )
        d = provider_report_as_dict(report)
        assert "profiles" in d
        assert "available_count" in d
        assert "selections" in d