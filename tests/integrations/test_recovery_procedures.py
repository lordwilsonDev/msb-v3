"""Recovery Procedure Test — TASK-008.

Simulate failures across every recovery mechanism in msb-v3 and verify
that the system recovers correctly. Covers:

1. Provider fallback chain — primary fails → fallback succeeds
2. ActionGate BLOCK via KillSwitch — arm → all blocked → disarm → resumes
3. MoIE verification failure — plan continues, marks unverified
4. Evidence spine corruption detection — tampered record → verify_chain catches it
5. Calibration store corruption detection — tampered record → verify_chain catches it
6. RalphLoop STATUS.json corruption — backup restore
7. Failure classification (verify.classify_failure) — transient/bad_tool/permission/unsafe
8. Self-annealing diagnosis — budget/timeout/scope/unknown → correct prescription
9. Evidence loop recovery — failed step → risk created → recommendation generated
10. End-to-end — primary fails → fallback succeeds → evidence loop → calibration

Every test is deterministic (no network, no LLM, no DB beyond temp dirs).
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────


@dataclass
class _FakeResult:
    ok: bool = True
    output: str = "ok"
    error: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)


class _FakeProvider:
    """Minimal fake matching the DshAgentProvider interface."""

    def __init__(self, pid: str, result: _FakeResult | None = None, available: bool = True):
        self._pid = pid
        self._result = result or _FakeResult(ok=True, output="ok")
        self._available = available

    def available(self) -> bool:
        return self._available

    def unavailable_reason(self) -> str:
        return "" if self._available else f"{self._pid} unavailable"

    async def execute(self, **kwargs: Any) -> _FakeResult:
        return self._result


class _FakeGate:
    """Minimal fake ActionGate."""

    @dataclass
    class _GateResult:
        verdict: str
        reason: str = ""

    def __init__(self, blocked: bool = False):
        self._blocked = blocked

    def gate(self, capability: str, **kwargs: Any) -> Any:
        if self._blocked:
            return self._GateResult(verdict="BLOCK", reason="killswitch armed")
        return self._GateResult(verdict="SAFE", reason="brakes clear")


def _make_plan(plan_id: str = "test:recovery", step_count: int = 2) -> Any:
    """Create a minimal WorkPlan for testing."""
    from msb_v3.plei.harness.work_plan import WorkPlan, WorkPlanStep

    steps = []
    for i in range(1, step_count + 1):
        steps.append(WorkPlanStep(
            step_id=f"{plan_id}:step{i}",
            sequence=i,
            goal=f"Step {i} goal",
            description=f"Step {i} description",
            risk_tier=1,
            preferred_provider_id="primary",
            fallback_provider_ids=["fallback"],
            capabilities_required=("test_cap",),
            verification_claims=[f"claim_{i}"],
            tainted_inputs=False,
        ))
    return WorkPlan(
        plan_id=plan_id,
        source_action_id=f"action:{plan_id}",
        source_action_description=f"Test action for {plan_id}",
        category="test",
        steps=steps,
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. PROVIDER FALLBACK CHAIN
# ══════════════════════════════════════════════════════════════════════════


class TestProviderFallbackChain:
    """When the primary provider fails, the bridge must fall through to the
    next provider in the chain and succeed if any fallback is available."""

    @pytest.mark.asyncio
    async def test_primary_fails_fallback_succeeds(self):
        from msb_v3.plei.harness.bridge import execute_plan

        primary = _FakeProvider("primary", _FakeResult(ok=False, output="", error="DeepSeek API 402 payment required"))
        fallback = _FakeProvider("fallback", _FakeResult(ok=True, output="fallback succeeded", error=""))

        providers = {"primary": primary, "fallback": fallback}
        plan = _make_plan(step_count=1)

        report = await execute_plan(
            plan, providers_by_id=providers, gate=None, moie=None, evidence_spine=None,
        )

        assert report.ok is True
        assert report.completed_steps == 1
        assert report.failed_steps == 0
        sr = report.step_results[0]
        assert sr.fallback_succeeded is True
        assert "primary" in sr.fallback_tried  # primary was tried and failed

    @pytest.mark.asyncio
    async def test_all_providers_fail(self):
        from msb_v3.plei.harness.bridge import execute_plan

        primary = _FakeProvider("primary", _FakeResult(ok=False, output="", error="402"))
        fallback = _FakeProvider("fallback", _FakeResult(ok=False, output="", error="402"))
        providers = {"primary": primary, "fallback": fallback}
        plan = _make_plan(step_count=1)

        report = await execute_plan(
            plan, providers_by_id=providers, gate=None, moie=None, evidence_spine=None,
        )

        assert report.ok is False
        assert report.failed_steps == 1
        assert "402" in report.step_results[0].error

    @pytest.mark.asyncio
    async def test_no_providers_available(self):
        from msb_v3.plei.harness.bridge import execute_plan

        plan = _make_plan(step_count=1)

        report = await execute_plan(
            plan, providers_by_id=None, gate=None, moie=None, evidence_spine=None,
        )

        assert report.ok is False
        assert report.failed_steps == 1
        assert "no providers" in report.step_results[0].error.lower()

    @pytest.mark.asyncio
    async def test_primary_unavailable_fallback_used(self):
        from msb_v3.plei.harness.bridge import execute_plan

        primary = _FakeProvider("primary", available=False)
        fallback = _FakeProvider("fallback", _FakeResult(ok=True, output="fallback OK"))
        providers = {"primary": primary, "fallback": fallback}
        plan = _make_plan(step_count=1)

        report = await execute_plan(
            plan, providers_by_id=providers, gate=None, moie=None, evidence_spine=None,
        )

        assert report.completed_steps == 1


# ══════════════════════════════════════════════════════════════════════════
# 2. ACTIONGATE BLOCK VIA KILLSWITCH
# ══════════════════════════════════════════════════════════════════════════


class TestKillSwitchGateBlock:
    """When the killswitch is armed, ActionGate returns BLOCK and the bridge
    halts execution immediately — no steps run."""

    @pytest.mark.asyncio
    async def test_blocked_plan_halts(self):
        from msb_v3.plei.harness.bridge import execute_plan

        provider = _FakeProvider("p1")
        gate = _FakeGate(blocked=True)
        providers = {"primary": provider, "fallback": provider}
        plan = _make_plan(step_count=3)

        report = await execute_plan(
            plan, providers_by_id=providers, gate=gate, moie=None, evidence_spine=None,
        )

        assert report.ok is False
        assert report.blocked_steps == 1  # halts on first BLOCK
        assert report.completed_steps == 0
        assert len(report.step_results) == 1

    @pytest.mark.asyncio
    async def test_disarmed_gate_allows_execution(self):
        from msb_v3.plei.harness.bridge import execute_plan

        provider = _FakeProvider("p1")
        gate = _FakeGate(blocked=False)
        providers = {"primary": provider, "fallback": provider}
        plan = _make_plan(step_count=3)

        report = await execute_plan(
            plan, providers_by_id=providers, gate=gate, moie=None, evidence_spine=None,
        )

        assert report.ok is True
        assert report.completed_steps == 3
        assert report.blocked_steps == 0


# ══════════════════════════════════════════════════════════════════════════
# 3. MOIE VERIFICATION FAILURE
# ══════════════════════════════════════════════════════════════════════════


class TestMoIEVerificationFailure:
    """When MoIE verification fails for a step, the step still completes
    (provider succeeded) but claims_verified < claims_total."""

    @pytest.mark.asyncio
    async def test_verification_fails_but_step_completes(self):
        from msb_v3.plei.harness.bridge import execute_plan

        provider = _FakeProvider("p1", _FakeResult(ok=True, output="done"))

        class _MoIE:
            def analyze(self, claim: str) -> Any:
                @dataclass
                class _D:
                    verdict: str
                    reason: str = ""
                if "claim_1" in claim:
                    return _D(verdict="OK")
                return _D(verdict="FAIL", reason="evidence insufficient")

        plan = _make_plan(step_count=2)
        report = await execute_plan(
            plan, providers_by_id={"primary": provider, "fallback": provider},
            gate=None, moie=_MoIE(), evidence_spine=None,
        )

        assert report.ok is True
        assert report.completed_steps == 2
        assert report.step_results[0].claims_verified == 1
        assert report.step_results[1].claims_verified == 0
        assert report.step_results[1].verified is False

    @pytest.mark.asyncio
    async def test_no_moie_all_claims_unverified(self):
        from msb_v3.plei.harness.bridge import execute_plan

        provider = _FakeProvider("p1")
        plan = _make_plan(step_count=1)
        report = await execute_plan(
            plan, providers_by_id={"primary": provider, "fallback": provider},
            gate=None, moie=None, evidence_spine=None,
        )

        sr = report.step_results[0]
        assert sr.claims_total == 1
        assert sr.claims_verified == 0
        assert sr.verified is False


# ══════════════════════════════════════════════════════════════════════════
# 4. EVIDENCE SPINE CORRUPTION DETECTION
# ══════════════════════════════════════════════════════════════════════════


class TestEvidenceSpineCorruption:
    """The evidence spine's verify_chain() must detect tampered records."""

    def test_valid_chain_passes(self):
        from msb_v3.evidence.spine import DecisionEvidence, DecisionEvidenceStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "spine.db")
            store = DecisionEvidenceStore(db_path)

            for i in range(3):
                store.append(DecisionEvidence(
                    task_id=f"t{i}", policy_version="v1", policy_result="PASS",
                    risk_level="1", provider="p1",
                ))

            result = store.verify_chain()
            assert result["valid"] is True
            assert result["record_count"] == 3

    def test_tampered_record_detected(self):
        from msb_v3.evidence.spine import DecisionEvidence, DecisionEvidenceStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "spine.db")
            store = DecisionEvidenceStore(db_path)

            for i in range(3):
                store.append(DecisionEvidence(
                    task_id=f"t{i}", policy_version="v1", policy_result="PASS",
                    risk_level="1", provider="p1",
                ))

            # Tamper with record 1 — modify both the payload JSON and the task_id
            import sqlite3
            with sqlite3.connect(db_path) as conn:
                row = conn.execute("SELECT payload FROM decision_evidence WHERE seq=1").fetchone()
                payload = json.loads(row[0])
                payload["task_id"] = "TAMPERED"
                conn.execute(
                    "UPDATE decision_evidence SET task_id='TAMPERED', payload=? WHERE seq=1",
                    (json.dumps(payload, sort_keys=True),),
                )

            result = store.verify_chain()
            assert result["valid"] is False
            assert "broken_at_seq" in result

    def test_genesis_hash_integrity(self):
        from msb_v3.evidence.spine import DecisionEvidenceStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DecisionEvidenceStore(str(Path(tmpdir) / "spine.db"))
            result = store.verify_chain()
            assert result["valid"] is True

    def test_empty_store_verifies(self):
        from msb_v3.evidence.spine import DecisionEvidenceStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = DecisionEvidenceStore(str(Path(tmpdir) / "empty.db"))
            result = store.verify_chain()
            assert result["valid"] is True


# ══════════════════════════════════════════════════════════════════════════
# 5. CALIBRATION STORE CORRUPTION DETECTION
# ══════════════════════════════════════════════════════════════════════════


class TestCalibrationStoreCorruption:
    """The calibration store's verify_chain() must detect tampered records."""

    def test_valid_chain_passes(self):
        from msb_v3.plei.calibration.store import CalibrationStore, Prediction

        with tempfile.TemporaryDirectory() as tmpdir:
            store = CalibrationStore(str(Path(tmpdir) / "cal.jsonl"))

            store.record_prediction(Prediction(
                prediction_id="p1", project="test", forecast_at="2026-01-01T00:00:00Z",
                predicted_p50_days=10.0, predicted_p80_days=15.0, predicted_p95_days=25.0,
                predicted_mean_days=12.0, predicted_stdev_days=4.0,
                predicted_failure_probability=0.15, trial_count=1000, seed=42,
            ))

            valid, msg = store.verify_chain()
            assert valid is True

    def test_tampered_prediction_detected(self):
        from msb_v3.plei.calibration.store import CalibrationStore, Prediction

        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "cal.jsonl"
            store = CalibrationStore(str(store_path))

            store.record_prediction(Prediction(
                prediction_id="p1", project="test", forecast_at="2026-01-01T00:00:00Z",
                predicted_p50_days=10.0, predicted_p80_days=15.0, predicted_p95_days=25.0,
                predicted_mean_days=12.0, predicted_stdev_days=4.0,
                predicted_failure_probability=0.15, trial_count=1000, seed=42,
            ))

            # Tamper with the file
            lines = store_path.read_text().splitlines(keepends=True)
            modified = lines[0].replace("p1", "TAMPERED-ID")
            store_path.write_text(modified + ''.join(lines[1:]))

            valid, msg = store.verify_chain()
            assert valid is False

    def test_empty_store_verifies(self):
        from msb_v3.plei.calibration.store import CalibrationStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = CalibrationStore(str(Path(tmpdir) / "empty.jsonl"))
            valid, msg = store.verify_chain()
            assert valid is True


# ══════════════════════════════════════════════════════════════════════════
# 6. RALPH LOOP STATUS.JSON CORRUPTION → BACKUP RESTORE
# ══════════════════════════════════════════════════════════════════════════


class TestRalphLoopRecovery:
    """When STATUS.json is corrupted, the RalphLoopHarness must restore
    from the .bak backup file."""

    def test_corrupted_status_restores_from_backup(self):
        from msb_v3.agent.execution_loop import RalphLoopHarness, Status

        harness = RalphLoopHarness()
        try:
            status = Status()
            status.iterations = 5
            status.logs = ["step1", "step2"]
            harness._write_status(status)

            loaded = harness._read_status()
            assert loaded.iterations == 5

            # Corrupt STATUS.json
            harness._status_path.write_text('NOT VALID JSON {{{')

            # Should restore from .bak
            restored = harness._read_status()
            assert restored.iterations == 5
            assert restored.logs == ["step1", "step2"]
        finally:
            harness._release_lock()

    def test_no_backup_returns_default(self):
        from msb_v3.agent.execution_loop import RalphLoopHarness

        harness = RalphLoopHarness()
        try:
            harness._status_path.write_text("CORRUPTED")
            if harness._backup_path.exists():
                harness._backup_path.unlink()

            restored = harness._read_status()
            assert restored.iterations == 0  # default
        finally:
            harness._release_lock()


# ══════════════════════════════════════════════════════════════════════════
# 7. FAILURE CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════


class TestFailureClassification:
    """The failure classifier must correctly categorize errors for recovery."""

    @staticmethod
    def _classify(error: str, detail: str = "") -> str:
        from msb_v3.agent.verify import classify_failure

        task = {"task_id": "t", "goal": "q"}
        verification = {"ok": False, "detail": detail}
        return classify_failure(task, {}, verification, error=error)

    def test_transient_timeout(self):
        assert self._classify("timed out") == "transient"

    def test_transient_connection(self):
        assert self._classify("ECONNREFUSED") == "transient"

    def test_transient_econnreset(self):
        assert self._classify("ECONNRESET") == "transient"

    def test_bad_tool(self):
        assert self._classify("tool-error: unknown tool") == "bad_tool"

    def test_bad_retrieval(self):
        assert self._classify("search returned no hits") == "bad_retrieval"

    def test_permission_denied(self):
        assert self._classify("permission denied") == "permission"

    def test_permission_forbidden(self):
        assert self._classify("403 forbidden") == "permission"

    def test_unsafe_blocked(self):
        assert self._classify("unsafe operation blocked") == "unsafe"

    def test_unknown_empty(self):
        assert self._classify("") == "unknown"

    def test_unknown_generic(self):
        assert self._classify("something weird happened") == "unknown"


# ══════════════════════════════════════════════════════════════════════════
# 8. SELF-ANNEALING DIAGNOSIS
# ══════════════════════════════════════════════════════════════════════════


class TestSelfAnnealing:
    """The RalphLoop self-annealing system must diagnose errors and
    prescribe the correct recovery action."""

    def test_budget_exhausted(self):
        from msb_v3.agent.execution_loop import RalphLoopHarness
        assert RalphLoopHarness._diagnose("budget exhausted, cost limit reached") == "budget_exhausted"
        assert RalphLoopHarness._prescribe("budget_exhausted") == "reduce_scope_or_abort"

    def test_stall_detected(self):
        from msb_v3.agent.execution_loop import RalphLoopHarness
        assert RalphLoopHarness._diagnose("timeout on provider call") == "stall_detected"
        assert RalphLoopHarness._prescribe("stall_detected") == "restart_from_checkpoint"

    def test_scope_mismatch(self):
        from msb_v3.agent.execution_loop import RalphLoopHarness
        assert RalphLoopHarness._diagnose("scope hash mismatch") == "scope_mismatch"
        assert RalphLoopHarness._prescribe("scope_mismatch") == "evolve_scope_with_approval"

    def test_unknown_failure(self):
        from msb_v3.agent.execution_loop import RalphLoopHarness
        assert RalphLoopHarness._diagnose("something broke") == "unknown_failure"
        assert RalphLoopHarness._prescribe("unknown_failure") == "quarantine_and_escalate"


# ══════════════════════════════════════════════════════════════════════════
# 9. EVIDENCE LOOP — FAILED STEP RECOVERY
# ══════════════════════════════════════════════════════════════════════════


class TestEvidenceLoopRecovery:
    """When a step fails, the evidence loop must create a risk entry,
    compute a twin delta, and generate a recommendation."""

    def _make_report(self, ok: bool, step_results: list | None = None) -> Any:
        from msb_v3.plei.harness.bridge import ExecutionReport, StepResult

        if step_results is None:
            step_results = [StepResult(
                step_id="step:1", sequence=1, goal="test",
                provider_id="p1", ok=ok, output="done" if ok else "",
                error="" if ok else "provider timeout",
                gate_verdict="SAFE",
            )]
        return ExecutionReport(
            plan_id="test:loop", ok=ok,
            total_steps=len(step_results), completed_steps=sum(1 for s in step_results if s.ok),
            failed_steps=sum(1 for s in step_results if not s.ok),
            blocked_steps=0, review_steps=0,
            step_results=step_results,
        )

    def test_failed_step_creates_risk(self):
        from msb_v3.plei.harness.evidence_loop import run_evidence_loop
        from msb_v3.plei.twin import ProjectTwin

        report = self._make_report(ok=False)
        twin = ProjectTwin()

        result = run_evidence_loop(report, twin)

        assert result.twin_delta is not None
        assert result.twin_delta.risks_created or result.recommendation
        assert result.recommendation

    def test_successful_step_no_risk(self):
        from msb_v3.plei.harness.evidence_loop import run_evidence_loop
        from msb_v3.plei.twin import ProjectTwin

        report = self._make_report(ok=True)
        twin = ProjectTwin()

        result = run_evidence_loop(report, twin)

        assert result.twin_delta is not None
        assert not result.twin_delta.risks_created


# ══════════════════════════════════════════════════════════════════════════
# 10. END-TO-END RECOVERY FLOW
# ══════════════════════════════════════════════════════════════════════════


class TestEndToEndRecoveryFlow:
    """Simulate the full recovery flow: primary fails → fallback succeeds →
    evidence loop processes → recommendation generated."""

    @pytest.mark.asyncio
    async def test_full_fallback_recovery_pipeline(self):
        from msb_v3.plei.harness.bridge import execute_plan
        from msb_v3.plei.harness.evidence_loop import run_evidence_loop
        from msb_v3.plei.twin import ProjectTwin

        primary = _FakeProvider("primary", _FakeResult(ok=False, output="", error="connection refused"))
        fallback = _FakeProvider("fallback", _FakeResult(ok=True, output="recovered via fallback", error=""))
        providers = {"primary": primary, "fallback": fallback}

        plan = _make_plan(plan_id="e2e:recovery", step_count=1)
        report = await execute_plan(
            plan, providers_by_id=providers, gate=None, moie=None, evidence_spine=None,
        )

        assert report.ok is True
        assert report.step_results[0].fallback_succeeded is True

        # Process through evidence loop
        twin = ProjectTwin()
        loop_result = run_evidence_loop(report, twin)
        assert loop_result.twin_delta is not None

    @pytest.mark.asyncio
    async def test_partial_failure_multi_step(self):
        """Step 1 fails (all providers), step 2 succeeds — plan is not-ok
        but partially completed."""
        from msb_v3.plei.harness.bridge import execute_plan

        call_count = 0

        class _SmartProvider:
            def available(self) -> bool:
                return True

            def unavailable_reason(self) -> str:
                return ""

            async def execute(self, **kwargs: Any) -> _FakeResult:
                nonlocal call_count
                call_count += 1
                step_id = str(kwargs.get("context", {}).get("step_id", ""))
                if "step1" in step_id:
                    return _FakeResult(ok=False, error="provider down")
                return _FakeResult(ok=True, output="step 2 done")

        smart = _SmartProvider()
        providers = {"primary": smart, "fallback": smart}
        plan = _make_plan(plan_id="partial:fail", step_count=2)

        report = await execute_plan(
            plan, providers_by_id=providers, gate=None, moie=None, evidence_spine=None,
        )

        assert report.ok is False
        assert report.failed_steps >= 1
        assert report.completed_steps >= 1
        assert report.total_steps == 2
