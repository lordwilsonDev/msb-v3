"""FlywheelEngine tests — the loop's first turn, behind every brake."""

from __future__ import annotations

from pathlib import Path

from msb_v3.governance.governor import OuroborosGovernor


def _drive_with_approvals(engine, turn_id: str):
    """Run a turn to completion, approving at each approval gate."""
    turn = engine.run(turn_id)
    steps = 0
    while turn.status == "WAITING_APPROVAL" and steps < 10:
        turn = engine.approve(turn_id, operator="wilson")
        steps += 1
    return turn


def test_full_turn_parks_and_completes(env) -> None:
    engine = env["engine"]
    turn = engine.start("Build a sovereign mesh for local-first agents", charger="stub")
    assert turn.status == "PENDING"

    turn = engine.run(turn.turn_id)
    assert turn.status == "WAITING_APPROVAL"  # parked at build
    assert "build" in turn.approval_ids
    assert turn.stage == "build"

    turn = _drive_with_approvals(engine, turn.turn_id)
    assert turn.status == "DONE"
    assert turn.uim_path and Path(turn.uim_path).exists()
    assert turn.blueprint_path and Path(turn.blueprint_path).exists()
    assert turn.record_path and Path(turn.record_path).exists()  # vault doc-trail
    assert "flywheel" in turn.record_path


def test_record_publishes_axiom_artifact(env) -> None:
    engine = env["engine"]
    turn = engine.start("Publish a test axiom", charger="stub")
    turn = _drive_with_approvals(engine, turn.turn_id)
    assert turn.status == "DONE"
    from msb_v3.uac.axiom_library import AxiomLibrary

    lib = AxiomLibrary(db_path=str(env["tmp_path"] / "axiom.db"))
    rec = lib.get(f"flywheel/{turn.turn_id}")
    assert rec is not None
    assert rec.payload["problem"] == "Publish a test axiom"


def test_every_stage_audited(env) -> None:
    engine = env["engine"]
    turn = engine.start("Audit every stage", charger="stub")
    turn = _drive_with_approvals(engine, turn.turn_id)
    assert turn.status == "DONE"
    records = env["chain"].get_chain(component="flywheel")
    event_types = [r.event_type for r in records]
    for stage in ("verify_novelty", "draft_blueprint", "charge", "scan_papers", "build", "combine", "record"):
        assert f"stage.{stage}" in event_types, f"missing audit for {stage}"
    assert "done" in event_types


def test_kill_switch_blocks_start(env) -> None:
    from msb_v3.governance.killswitch import KillSwitch

    KillSwitch(db_path=str(env["tmp_path"] / "ks.db"),
               audit_chain=env["chain"]).arm("wilson", "stop everything")
    turn = env["engine"].start("Anything", charger="stub")
    assert turn.status == "BLOCKED"
    assert turn.notes[-1].startswith("start refused")


def test_budget_halts_charge(env, rebuild) -> None:
    from msb_v3.governance.budget import BudgetLedger

    # Rebuild the engine over a zero research-calls ledger: verify+draft pass
    # (iterations only), charge is refused by the budget brake.
    zero = BudgetLedger(
        db_path=str(env["tmp_path"] / "budget.db"),
        limits={"research_calls": 0, "tokens": 1000, "iterations": 50},
        window_s=3600,
    )
    engine = rebuild(ledger=zero)
    turn = engine.start("Spend the research budget", charger="stub")
    turn = engine.run(turn.turn_id)
    assert turn.status == "HALTED"
    assert turn.stage == "charge"
    assert any("budget cap hit" in n for n in turn.notes)


def test_novelty_gate_stops_before_build(env) -> None:
    env["engine"]._vault_novelty = lambda problem: 0.99  # vault already covers it
    turn = env["engine"].start("Already documented thing", charger="stub")
    turn = env["engine"].run(turn.turn_id)
    assert turn.status == "ALREADY_EXISTS"
    assert turn.blueprint_path is None  # nothing built
    assert turn.approval_ids == {}  # no approvals ever submitted


def test_restart_survival(env, rebuild) -> None:
    engine = env["engine"]
    turn = engine.start("Survives a restart", charger="stub")
    turn = engine.run(turn.turn_id)
    assert turn.status == "WAITING_APPROVAL"  # parked at build

    # "Restart": a brand-new engine over the same paths.
    engine2 = rebuild()
    parked = engine2.get(turn.turn_id)
    assert parked.status == "WAITING_APPROVAL"
    turn = _drive_with_approvals(engine2, turn.turn_id)
    assert turn.status == "DONE"
    assert turn.record_path is not None


def test_governor_halts_charge(env, rebuild) -> None:
    # Ouroboros with stall_limit=1 and a high novelty floor: the charge
    # signal (novelty ~0 offline) halts the turn.
    governor = OuroborosGovernor(
        db_path=str(env["tmp_path"] / "gov.db"), stall_limit=1, novelty_min=0.5
    )
    engine = rebuild(governor=governor)
    # Deterministic novelty: force it below the floor (0.5) so the governor
    # stalls on the first iteration regardless of the live vault's /rag/search.
    engine._vault_novelty = lambda problem: 0.0  # type: ignore[method-assign]
    turn = engine.start("Governor should halt me", charger="stub")
    turn = engine.run(turn.turn_id)
    assert turn.status == "HALTED"
    assert turn.stage == "charge"
