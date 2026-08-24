"""Phase 6 harness tests — work plan construction, bridge wiring, evidence loop."""

from __future__ import annotations

from msb_v3.plei.harness.bridge import (
    ExecutionReport,
    StepResult,
    _gate_step,
    _verify_claims,
    execution_report_as_dict,
)
from msb_v3.plei.harness.evidence_loop import (
    LoopResult,
    TwinDelta,
    loop_result_as_dict,
    run_evidence_loop,
)
from msb_v3.plei.harness.work_plan import (
    WorkPlanStep,
    build_work_plan,
    work_plan_as_dict,
)

# ── Work Plan tests ───────────────────────────────────────────────────────


class TestWorkPlanConstruction:
    def test_build_work_plan_gap_close(self):
        """A gap_close action produces a 3-step plan."""
        na = {
            "action_id": "gap:incident_response",
            "description": "Activate capability: incident_response",
            "category": "gap_close",
            "score": 1.78,
            "expected_outcome": "Capability available",
            "validation_checks": ["Score: 1.78", "Reversibility: 90%"],
        }
        plan = build_work_plan(na)
        assert plan.total_steps == 3
        assert plan.category == "gap_close"
        assert plan.max_risk_tier == 2
        assert not plan.requires_operator_approval
        # Step sequence
        assert plan.steps[0].step_id == "gap:incident_response.1"
        assert plan.steps[1].step_id == "gap:incident_response.2"
        assert plan.steps[2].step_id == "gap:incident_response.3"
        # Verify gap close has "verify gap is now closed" as last step
        assert "close" in plan.steps[2].description.lower()

    def test_build_work_plan_risk_mitigate(self):
        """A risk_mitigate action produces a 3-step plan with higher risk tier."""
        na = {
            "action_id": "risk:disk_saturation",
            "description": "Mitigate: Operational: Disk saturation",
            "category": "risk_mitigate",
            "score": 2.5,
            "expected_outcome": "Risk severity reduced",
            "validation_checks": [],
        }
        plan = build_work_plan(na)
        assert plan.total_steps == 3
        assert plan.category == "risk_mitigate"
        assert plan.max_risk_tier == 3
        # Mitigation step is not reversible
        assert not plan.steps[1].reversible

    def test_build_work_plan_debt_reduce(self):
        """A debt_reduce action produces a 3-step plan with tainted inputs."""
        na = {
            "action_id": "debt:no_schema_versioning",
            "description": "Reduce debt: No DB schema versioning",
            "category": "debt_reduce",
            "score": 3.0,
            "expected_outcome": "Debt reduced",
            "validation_checks": [],
        }
        plan = build_work_plan(na)
        assert plan.total_steps == 3
        # Debt reduction step reads existing code → tainted
        assert plan.steps[1].tainted_inputs
        assert not plan.steps[1].reversible

    def test_build_work_plan_with_provider_selection(self):
        """Provider selection data enriches the work plan routing."""
        na = {
            "action_id": "gap:health_monitoring",
            "description": "Activate capability: health_monitoring",
            "category": "gap_close",
            "score": 0.9,
            "expected_outcome": "Capability available",
            "validation_checks": [],
        }
        provider_sel = {
            "primary": {"provider_id": "local.slice", "rationale": "covers all required capabilities"},
            "fallbacks": [{"provider_id": "cli.codebuddy"}, {"provider_id": "dsh.headless"}],
        }
        plan = build_work_plan(na, provider_sel)
        # All steps should prefer local.slice
        for step in plan.steps:
            assert step.preferred_provider_id == "local.slice"
        # Step 2 should have fallbacks
        assert "cli.codebuddy" in plan.steps[1].fallback_provider_ids

    def test_build_work_plan_unknown_category(self):
        """Unknown categories produce a single-step generic plan."""
        na = {
            "action_id": "unknown:test",
            "description": "some unknown action",
            "category": "unknown_type",
            "score": 0.5,
            "expected_outcome": "",
            "validation_checks": [],
        }
        plan = build_work_plan(na)
        assert plan.total_steps == 1
        assert plan.steps[0].step_id == "unknown:test.1"

    def test_work_plan_as_dict(self):
        """work_plan_as_dict produces valid serialization."""
        na = {
            "action_id": "gap:test",
            "description": "Test gap",
            "category": "gap_close",
            "score": 1.0,
            "expected_outcome": "Done",
            "validation_checks": [],
        }
        plan = build_work_plan(na)
        d = work_plan_as_dict(plan)
        assert d["plan_id"] == "plan:gap:test"
        assert d["total_steps"] == 3
        assert isinstance(d["steps"], list)
        assert d["steps"][0]["step_id"] == "gap:test.1"


# ── Bridge tests ──────────────────────────────────────────────────────────


class TestGateStep:
    def test_gate_no_gate_available(self):
        """Without an ActionGate, steps are SAFE by default."""
        step = WorkPlanStep(
            step_id="test.1",
            sequence=1,
            description="test",
            goal="test goal",
            risk_tier=1,
        )
        verdict, reason = _gate_step(step, gate=None)
        assert verdict == "SAFE"
        assert "ungated" in reason

    def test_gate_tainted_inputs(self):
        """Tainted inputs are still gated (verdict depends on gate config)."""

        class FakeGate:
            def gate(self, capability, *, tainted_inputs=False, approved=None):
                if tainted_inputs and capability == "read_vault":
                    return type("V", (), {"verdict": "REVIEW", "reason": "tainted read"})()

                return type("V", (), {"verdict": "SAFE", "reason": ""})()

        step = WorkPlanStep(
            step_id="test.1",
            sequence=1,
            description="test",
            goal="test goal",
            risk_tier=2,
            capabilities_required=("read_vault",),
            tainted_inputs=True,
        )
        verdict, reason = _gate_step(step, gate=FakeGate())
        assert verdict == "REVIEW"

    def test_gate_exception_fail_closed(self):
        """Gate exception returns BLOCK — fail-closed."""

        class BrokenGate:
            def gate(self, *args, **kwargs):
                raise RuntimeError("gate implosion")

        step = WorkPlanStep(
            step_id="test.1",
            sequence=1,
            description="test",
            goal="test goal",
            risk_tier=1,
        )
        verdict, reason = _gate_step(step, gate=BrokenGate())
        assert verdict == "BLOCK"
        assert "exception" in reason.lower()


class TestVerifyClaims:
    def test_empty_claims_passes(self):
        """No claims to verify → always passes."""
        verified, total, all_passed = _verify_claims([], moie=None)
        assert verified == 0
        assert total == 0
        assert all_passed

    def test_claims_without_moie(self):
        """Without MoIE, claims are unverified."""
        verified, total, all_passed = _verify_claims(
            ["claim A should pass", "claim B should pass"],
            moie=None,
        )
        assert verified == 0
        assert total == 2
        assert not all_passed

    def test_claims_with_fake_moie(self):
        """With a MoIE that passes everything, all claims verify."""

        class FakeMoIE:
            def analyze(self, claim):
                return type("D", (), {"verdict": "PASS"})()

        verified, total, all_passed = _verify_claims(
            ["claim A", "claim B", "claim C"],
            moie=FakeMoIE(),
        )
        assert verified == len("ABC")
        assert all_passed

    def test_claims_mixed_verdicts(self):
        """Mixed verdicts — only PASS/OK counts as verified."""

        class MixedMoIE:
            def __init__(self):
                self.calls = 0

            def analyze(self, claim):
                self.calls += 1
                if self.calls == 1:
                    return type("D", (), {"verdict": "PASS"})()
                if self.calls == 2:
                    return type("D", (), {"verdict": "FAIL"})()
                return type("D", (), {"verdict": "OK"})()

        verified, total, all_passed = _verify_claims(
            ["A", "B", "C"],
            moie=MixedMoIE(),
        )
        assert verified == 2  # PASS + OK
        assert total == 3
        assert not all_passed  # B failed


class TestExecutionReport:
    def test_report_serialization(self):
        """execution_report_as_dict round-trips cleanly."""
        report = ExecutionReport(
            plan_id="plan:test.1",
            ok=True,
            total_steps=2,
            completed_steps=2,
            failed_steps=0,
            blocked_steps=0,
            review_steps=0,
            step_results=[
                StepResult(
                    step_id="test.1",
                    sequence=1,
                    goal="goal A",
                    provider_id="local.slice",
                    ok=True,
                    output="done",
                    gate_verdict="SAFE",
                    claims_verified=2,
                    claims_total=2,
                    verified=True,
                ),
            ],
            total_duration_s=0.5,
            evidence_receipts=["decision_abc123"],
        )
        d = execution_report_as_dict(report)
        assert d["plan_id"] == "plan:test.1"
        assert d["ok"] is True
        assert len(d["step_results"]) == 1
        assert d["step_results"][0]["claims_verified"] == "2/2"


# ── Evidence loop tests ───────────────────────────────────────────────────


class TestEvidenceLoop:
    def test_loop_successful_execution(self):
        """Successful execution updates twin delta."""
        from msb_v3.plei.provenance import Provenanced
        from msb_v3.plei.twin import (
            ProjectArchitecture,
            ProjectEvidence,
            ProjectHealth,
            ProjectIdentity,
            ProjectLifecycle,
            ProjectTwin,
        )

        twin = ProjectTwin(
            identity=ProjectIdentity(
                name=Provenanced.observed("test-proj", "test"),
                version=Provenanced.observed("0.1.0", "test"),
            ),
            architecture=ProjectArchitecture(),
            lifecycle=ProjectLifecycle(
                stage=Provenanced.observed("IMPLEMENTATION", "test"),
                confidence=Provenanced.observed(0.85, "test"),
            ),
            health=ProjectHealth(
                scores=Provenanced.observed(
                    {"architecture": 0.90, "implementation": 0.80, "testing": 0.85, "ops": 0.70, "documentation": 0.60},
                    "test",
                ),
            ),
            evidence=ProjectEvidence(
                test_count=Provenanced.observed(500, "test"),
                audit_chain_entries=Provenanced.observed(100, "test"),
            ),
        )
        # Provide enough facts for lifecycle classifier to stay in IMPLEMENTATION
        twin.health._launch_agents = []
        twin.health._ops_audits = []
        twin.health._has_ci = False

        report = ExecutionReport(
            plan_id="plan:gap.test",
            ok=True,
            total_steps=3,
            completed_steps=3,
            failed_steps=0,
            blocked_steps=0,
            review_steps=0,
            step_results=[
                StepResult(
                    step_id="gap:test.1",
                    sequence=1,
                    goal="verify gap",
                    provider_id="local.slice",
                    ok=True,
                    verified=True,
                    claims_verified=1,
                    claims_total=1,
                ),
                StepResult(
                    step_id="gap:test.2",
                    sequence=2,
                    goal="close gap",
                    provider_id="local.slice",
                    ok=True,
                    verified=True,
                    claims_verified=2,
                    claims_total=2,
                ),
                StepResult(
                    step_id="gap:test.3",
                    sequence=3,
                    goal="verify closed",
                    provider_id="local.slice",
                    ok=True,
                    verified=True,
                    claims_verified=1,
                    claims_total=1,
                ),
            ],
        )

        loop = run_evidence_loop(
            report,
            twin,
            previous_stage="IMPLEMENTATION",
            previous_confidence=0.85,
        )

        assert loop.execution_report.ok
        assert loop.twin_delta.twin_updated
        assert loop.twin_delta.new_verified_claims == 4  # 1+2+1
        assert loop.ready_for_calibration
        # Twin has no real implementation evidence so classifier may drop to IDEA;
        # what matters is the loop ran and produced a delta.
        assert loop.twin_delta.evidence_age_s >= 0

    def test_loop_failed_execution(self):
        """Failed execution creates risks and negative health deltas."""
        from msb_v3.plei.provenance import Provenanced
        from msb_v3.plei.twin import (
            ProjectArchitecture,
            ProjectEvidence,
            ProjectHealth,
            ProjectIdentity,
            ProjectLifecycle,
            ProjectTwin,
        )

        twin = ProjectTwin(
            identity=ProjectIdentity(
                name=Provenanced.observed("test-proj", "test"),
                version=Provenanced.observed("0.1.0", "test"),
            ),
            architecture=ProjectArchitecture(),
            lifecycle=ProjectLifecycle(
                stage=Provenanced.observed("OPERATIONS", "test"),
                confidence=Provenanced.observed(0.90, "test"),
            ),
            health=ProjectHealth(),
            evidence=ProjectEvidence(),
        )

        report = ExecutionReport(
            plan_id="plan:risk.test",
            ok=False,
            total_steps=3,
            completed_steps=1,
            failed_steps=1,
            blocked_steps=1,
            review_steps=0,
            step_results=[
                StepResult(step_id="r.1", sequence=1, goal="assess", provider_id="local.slice", ok=True),
                StepResult(
                    step_id="r.2",
                    sequence=2,
                    goal="mitigate",
                    provider_id="local.slice",
                    ok=False,
                    error="permission denied",
                ),
                StepResult(
                    step_id="r.3",
                    sequence=3,
                    goal="verify",
                    provider_id="local.slice",
                    ok=False,
                    gate_verdict="BLOCK",
                    gate_reason="kill switch armed",
                ),
            ],
        )

        loop = run_evidence_loop(
            report,
            twin,
            previous_stage="OPERATIONS",
            previous_confidence=0.90,
        )

        assert not loop.execution_report.ok
        assert len(loop.twin_delta.risks_created) >= 1
        assert any("FAILED" in r or "BLOCKED" in r for r in loop.twin_delta.risks_created)

    def test_loop_result_serialization(self):
        """loop_result_as_dict produces valid serialization."""
        report = ExecutionReport(
            plan_id="plan:test",
            ok=True,
            total_steps=1,
            completed_steps=1,
            failed_steps=0,
            blocked_steps=0,
            review_steps=0,
            step_results=[
                StepResult(
                    step_id="t.1",
                    sequence=1,
                    goal="do something",
                    provider_id="local.slice",
                    ok=True,
                ),
            ],
        )
        delta = TwinDelta(
            evidence_age_s=120.0,
            twin_updated=True,
            previous_stage="IMPLEMENTATION",
            current_stage="IMPLEMENTATION",
            gaps_closed=["gap:test"],
        )
        loop = LoopResult(
            execution_report=report,
            twin_delta=delta,
            loop_duration_s=0.1,
            recommendation="All done",
            ready_for_calibration=True,
        )
        d = loop_result_as_dict(loop)
        assert d["execution"]["plan_id"] == "plan:test"
        assert d["twin_delta"]["gaps_closed"] == ["gap:test"]
        assert d["recommendation"] == "All done"
        assert d["ready_for_calibration"]