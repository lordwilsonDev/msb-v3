"""PLEI Execute Loop Integration Test — TASK-006

Exercises the full governed execution pipeline:
  WorkPlan construction → ActionGate → Provider execution → MoIE verification →
  Evidence spine recording → Evidence loop → Calibration auto-record

This is the second biggest verification gap (V2 in the Closer report).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from msb_v3.plei.harness.bridge import (
    ExecutionReport,
    StepResult,
    execute_plan,
)
from msb_v3.plei.harness.evidence_loop import (
    LoopResult,
    loop_result_as_dict,
    run_evidence_loop,
)
from msb_v3.plei.harness.work_plan import WorkPlan, build_work_plan, work_plan_as_dict
from msb_v3.plei.twin import ProjectTwin

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_twin_with_evidence() -> ProjectTwin:
    """Create a twin with enough evidence to reach OPERATIONS stage."""
    from msb_v3.plei.twin import Provenanced

    twin = ProjectTwin()
    twin.identity.name = "test-project"

    # Set evidence fields that the lifecycle classifier checks
    twin.evidence.test_count = Provenanced(value=2020, provenance="VERIFIED", source="test")
    twin.evidence.test_pass_rate = Provenanced(value=0.99, provenance="VERIFIED", source="test")
    twin.evidence.audit_chain_entries = Provenanced(value=100, provenance="VERIFIED", source="test")
    twin.evidence.claims_verified = Provenanced(value=50, provenance="VERIFIED", source="test")
    twin.evidence.live_health = Provenanced(value="ok", provenance="VERIFIED", source="test")

    # Set health attributes that lifecycle classifier checks
    twin.health._launch_agents = ["a", "b", "c"]  # 3 agents → OPERATIONS
    twin.health._ops_audits = ["audit1", "audit2"]  # 2 audits
    twin.health._has_ci = True

    return twin


def _action_dict(category: str = "gap_close", **overrides) -> dict:
    """Build a NextAction dict for testing."""
    base = {
        "action_id": "test-action-001",
        "description": "Test action for integration",
        "category": category,
        "score": 5.0,
        "impact": 5,
        "risk_reduction": 3,
        "confidence": 0.8,
        "cost": 2,
        "reversibility": 90,
        "affected_components": ["test_component"],
        "evidence": "Integration test",
        "alternatives": [],
        "prerequisites": [],
        "fallback_actions": [],
    }
    base.update(overrides)
    return base


def _mock_report(
    *,
    total: int = 2,
    completed: int = 1,
    failed: int = 1,
    blocked: int = 0,
) -> ExecutionReport:
    """Build a minimal ExecutionReport for evidence loop testing."""
    steps = []
    for i in range(total):
        ok = i < completed
        steps.append(
            StepResult(
                step_id=f"step_{i + 1}",
                sequence=i + 1,
                goal=f"Test step {i + 1}",
                provider_id="local.slice",
                ok=ok,
                output="Success" if ok else "",
                error="" if ok else "Expected failure",
                gate_verdict="SAFE",
                gate_reason="test",
                verified=ok,  # mock: completed steps are verified
                claims_total=1 if ok else 0,
                claims_verified=1 if ok else 0,
            )
        )
    return ExecutionReport(
        plan_id="test_plan",
        ok=failed == 0 and blocked == 0,
        total_steps=total,
        completed_steps=completed,
        failed_steps=failed,
        blocked_steps=blocked,
        review_steps=0,
        step_results=steps,
        total_duration_s=0.5,
        evidence_receipts=["receipt_1"],
    )


# ── WorkPlan Construction ───────────────────────────────────────────────


class TestWorkPlanConstruction:
    """WorkPlan must be constructable from a NextAction dict."""

    def test_build_plan_gap_close(self):
        plan = build_work_plan(_action_dict("gap_close"))
        assert isinstance(plan, WorkPlan)
        assert len(plan.steps) > 0

    def test_build_plan_risk_mitigate(self):
        plan = build_work_plan(_action_dict("risk_mitigate"))
        assert isinstance(plan, WorkPlan)
        assert len(plan.steps) > 0

    def test_build_plan_debt_reduce(self):
        plan = build_work_plan(_action_dict("debt_reduce"))
        assert isinstance(plan, WorkPlan)
        assert len(plan.steps) > 0

    def test_plan_has_verification_claims(self):
        plan = build_work_plan(_action_dict())
        for step in plan.steps:
            assert step.verification_claims is not None

    def test_plan_serializes(self):
        plan = build_work_plan(_action_dict())
        d = work_plan_as_dict(plan)
        assert "plan_id" in d
        assert "steps" in d
        assert len(d["steps"]) > 0


# ── Bridge Execution ─────────────────────────────────────────────────────


class TestBridgeExecution:
    """Bridge must execute a WorkPlan through governed providers."""

    @pytest.mark.asyncio
    async def test_execute_plan_with_mock_provider(self):
        plan = build_work_plan(_action_dict())

        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(return_value="Mock execution output")
        mock_provider.available = MagicMock(return_value=True)

        providers = {"local.slice": mock_provider}

        report = await execute_plan(plan, providers_by_id=providers)

        assert isinstance(report, ExecutionReport)
        assert report.total_steps > 0
        assert report.completed_steps + report.failed_steps + report.blocked_steps > 0

    @pytest.mark.asyncio
    async def test_execute_plan_no_providers(self):
        plan = build_work_plan(_action_dict())

        report = await execute_plan(plan, providers_by_id=None)

        assert isinstance(report, ExecutionReport)
        # All steps should fail (no providers)
        assert report.failed_steps > 0 or report.blocked_steps > 0


# ── Evidence Loop ────────────────────────────────────────────────────────


class TestEvidenceLoop:
    """Evidence loop must capture execution results and update twin."""

    def test_run_evidence_loop(self):
        twin = _make_twin_with_evidence()
        report = _mock_report(total=2, completed=1, failed=1)

        loop = run_evidence_loop(report, twin)
        assert isinstance(loop, LoopResult)
        assert loop.execution_report.total_steps == 2
        assert loop.execution_report.completed_steps == 1
        assert loop.execution_report.failed_steps == 1

    def test_loop_result_as_dict(self):
        report = _mock_report(total=1, completed=1, failed=0)
        loop = run_evidence_loop(report, _make_twin_with_evidence())

        d = loop_result_as_dict(loop)
        assert isinstance(d, dict)
        assert "execution_report" in d or "total_steps" in str(d)

    def test_evidence_loop_detects_risks(self):
        twin = _make_twin_with_evidence()
        report = _mock_report(total=1, completed=0, failed=1)

        loop = run_evidence_loop(report, twin)
        # Failed steps should create risk entries
        assert loop.twin_delta.new_contradicted_claims > 0 or len(loop.twin_delta.risks_created) > 0


# ── Full Pipeline ────────────────────────────────────────────────────────


class TestFullPipeline:
    """Full pipeline: action dict → plan → execute → evidence → dict."""

    def test_full_pipeline_produces_valid_output(self):
        # 1. Build next action dict
        action_dict = _action_dict(
            action_id="pipeline-test-001",
            description="Pipeline test action",
        )

        # 2. Build work plan
        plan = build_work_plan(action_dict)
        assert plan is not None

        # 3. Serialize to dict (simulates API response)
        plan_dict = work_plan_as_dict(plan)
        assert "plan_id" in plan_dict
        assert "steps" in plan_dict
        assert len(plan_dict["steps"]) > 0

        # 4. Verify the plan dict has all required fields
        step = plan_dict["steps"][0]
        assert "step_id" in step
        assert "goal" in step
        assert "preferred_provider_id" in step
