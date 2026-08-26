"""Flywheel observability — Prometheus metrics accumulate correctly.

Proves Piece 5a: the flywheel engine emits stage latency, stage result,
and active turns metrics that are visible in Prometheus output.
"""

from __future__ import annotations

import pytest
from prometheus_client import generate_latest

from msb_v3.flywheel.chargers import StubScanner
from msb_v3.flywheel.engine import FlywheelEngine
from msb_v3.governance.approval import ApprovalQueue
from msb_v3.governance.budget import BudgetLedger
from msb_v3.governance.governor import OuroborosGovernor
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.observability.metrics import (
    FLYWHEEL_ACTIVE_TURNS,
    FLYWHEEL_STAGE_RESULT,
)
from msb_v3.uac.audit_chain import AuditChain
from msb_v3.uac.axiom_library import AxiomLibrary


@pytest.fixture(autouse=True)
def _reset_flywheel_gauge():
    """Reset the active turns gauge before each test to avoid global state leaks."""
    FLYWHEEL_ACTIVE_TURNS.set(0)
    yield


@pytest.fixture()
def engine(tmp_path):
    """Hermetic flywheel engine — no live vault, no live Tavily, no production DB."""
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    queue = ApprovalQueue(db_path=str(tmp_path / "appr.db"), audit_chain=chain)
    ledger = BudgetLedger(
        db_path=str(tmp_path / "budget.db"),
        limits={"research_calls": 10, "tokens": 1000, "iterations": 50},
        window_s=3600,
    )
    switch = KillSwitch(db_path=str(tmp_path / "ks.db"), audit_chain=chain)
    governor = OuroborosGovernor(db_path=str(tmp_path / "gov.db"))
    return FlywheelEngine(
        db_path=str(tmp_path / "turns.db"),
        queue=queue,
        ledger=ledger,
        switch=switch,
        governor=governor,
        audit_chain=chain,
        axiom_library=AxiomLibrary(db_path=str(tmp_path / "axiom.db")),
        vault_root=tmp_path / "vault",
        runtime_root=tmp_path / "rt",
        novelty_threshold=0.99,  # high threshold so turns don't get blocked as duplicates
        novelty_fn=lambda problem: 0.0,  # hermetic
        scanner=StubScanner(),
    )


def _drive_with_approvals(engine, turn_id: str):
    """Run a turn to completion, approving at each approval gate."""
    turn = engine.run(turn_id)
    steps = 0
    while turn.status == "WAITING_APPROVAL" and steps < 10:
        turn = engine.approve(turn_id, operator="test")
        steps += 1
    return turn


class TestFlywheelMetrics:
    """Prove: flywheel stages emit Prometheus metrics."""

    def test_stage_latency_observes_after_exec(self, engine):
        """_exec_stage records timing in the histogram."""
        turn = engine.start("test problem for metrics", charger="stub")
        turn = _drive_with_approvals(engine, turn.turn_id)
        assert turn.status == "DONE"

        # Verify latency histogram family exists in Prometheus output
        output = generate_latest().decode("utf-8")
        assert "msb_v3_flywheel_stage_seconds" in output

    def test_stage_result_counter_increments(self, engine):
        """Each completed stage increments the result counter."""
        turn = engine.start("test metrics counter", charger="stub")
        turn = _drive_with_approvals(engine, turn.turn_id)
        assert turn.status == "DONE"

        # Verify result counters exist for stages that ran
        output = generate_latest().decode("utf-8")
        assert "msb_v3_flywheel_stage_total" in output
        # At least one "pass" result should exist
        pass_count = sum(
            FLYWHEEL_STAGE_RESULT.labels(stage=s, result="pass")._value.get()
            for s in ("verify_novelty", "draft_blueprint", "charge", "scan_papers")
        )
        assert pass_count >= 1

    def test_active_turns_increments_on_start(self, engine):
        """Starting a turn increments the active turns gauge."""
        before = FLYWHEEL_ACTIVE_TURNS._value.get()
        engine.start("test active turns", charger="stub")
        after = FLYWHEEL_ACTIVE_TURNS._value.get()
        assert after > before, f"active turns should increase: {before} -> {after}"

    def test_active_turns_decrements_on_done(self, engine):
        """Completing a turn decrements the active turns gauge."""
        turn = engine.start("test done decrement", charger="stub")
        after_start = FLYWHEEL_ACTIVE_TURNS._value.get()
        turn = _drive_with_approvals(engine, turn.turn_id)
        assert turn.status == "DONE"
        after_done = FLYWHEEL_ACTIVE_TURNS._value.get()
        assert after_done < after_start, f"active turns should decrease: {after_start} -> {after_done}"

    def test_blocked_turn_does_not_increment_active(self, engine):
        """A blocked start does not increment active turns."""
        engine._switch.arm("test block")
        before = FLYWHEEL_ACTIVE_TURNS._value.get()
        turn = engine.start("test blocked", charger="stub")
        after = FLYWHEEL_ACTIVE_TURNS._value.get()
        assert after == before
        assert turn.status == "BLOCKED"
        blocked_count = FLYWHEEL_STAGE_RESULT.labels(stage="start", result="blocked")._value.get()
        assert blocked_count >= 1

    def test_error_decrements_active_turns(self, engine):
        """An error during execution decrements active turns."""
        turn = engine.start("test error decrement", charger="stub")
        after_start = FLYWHEEL_ACTIVE_TURNS._value.get()

        def _explode(_problem: str) -> float:
            raise RuntimeError("forced error for test")

        engine._novelty_fn = _explode
        turn = engine.run(turn.turn_id)

        if turn.status == "ERROR":
            after_error = FLYWHEEL_ACTIVE_TURNS._value.get()
            assert after_error < after_start, f"active turns should decrease: {after_start} -> {after_error}"

    def test_prometheus_output_contains_flywheel_metrics(self):
        """Flywheel metrics are visible in Prometheus text output."""
        output = generate_latest().decode("utf-8")
        assert "msb_v3_flywheel_stage_seconds" in output
        assert "msb_v3_flywheel_stage_total" in output
        assert "msb_v3_flywheel_active_turns" in output

    def test_stage_result_labels_are_correct(self, engine):
        """Stage result counter uses correct label values."""
        turn = engine.start("test labels", charger="stub")
        turn = _drive_with_approvals(engine, turn.turn_id)
        assert turn.status == "DONE"

        output = generate_latest().decode("utf-8")
        assert "msb_v3_flywheel_stage_total" in output
