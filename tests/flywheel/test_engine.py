"""FlywheelEngine tests — the loop's first turn, behind every brake."""

from __future__ import annotations

import json
import threading
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


def test_kill_switch_armed_mid_run_halts_loop(env) -> None:
    """The convergence ask: the kill switch must stop a turn that is ALREADY
    in flight, not just refuse new starts. Arm it after ``start``; the next
    stage transition re-checks the guard and HALT is enforced with an
    audited, explainable reason — never an invisible continuation."""
    from msb_v3.governance.killswitch import KillSwitch

    turn = env["engine"].start("Mid-flight kill", charger="stub")
    assert turn.status == "PENDING"

    # Arm the switch AFTER the start gate passed.
    KillSwitch(db_path=str(env["tmp_path"] / "ks.db"),
               audit_chain=env["chain"]).arm("wilson", "stop mid-run")

    turn = env["engine"].run(turn.turn_id)
    assert turn.status == "HALTED"
    assert any("halted" in n for n in turn.notes)
    assert any("kill" in n.lower() for n in turn.notes)

    # The halt is on the audit chain — explainable, not a black box.
    event_types = {e.event_type for e in env["chain"].get_chain("flywheel")}
    assert "halted" in event_types


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


def test_novelty_gate_stops_before_build(env, rebuild) -> None:
    engine = rebuild(novelty_fn=lambda problem: 0.99)  # vault already covers it
    turn = engine.start("Already documented thing", charger="stub")
    turn = engine.run(turn.turn_id)
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
    # novelty_fn is fixture-injected to 0.0 (below the 0.5 floor), so the
    # governor stalls on the first iteration deterministically.
    engine = rebuild(governor=governor)
    turn = engine.start("Governor should halt me", charger="stub")
    turn = engine.run(turn.turn_id)
    assert turn.status == "HALTED"
    assert turn.stage == "charge"


def test_concurrent_runs_claim_once(env, rebuild) -> None:
    """Two drivers calling run() at once: the status CAS lets exactly one
    claim the turn — no double stage execution, no duplicate approval items."""
    engine = rebuild()
    turn = engine.start("Race the drivers", charger="stub")

    barrier = threading.Barrier(2)
    results: list = []

    def drive() -> None:
        barrier.wait()
        try:
            results.append(engine.run(turn.turn_id))
        except Exception as exc:  # noqa: BLE001 — capture, don't crash the thread
            results.append(exc)

    t1 = threading.Thread(target=drive)
    t2 = threading.Thread(target=drive)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert len(results) == 2
    finals = [r.status for r in results if hasattr(r, "status")]
    assert finals, "both drivers returned turns"
    assert any(s == "WAITING_APPROVAL" for s in finals)  # winner parked at build

    # Exactly one driver claimed the turn: one 'UIM charged' note, no ERROR,
    # and approval ids have no duplicates.
    final = engine.get(turn.turn_id)
    assert final.status == "WAITING_APPROVAL"
    charged = [n for n in final.notes if n.startswith("UIM charged")]
    assert len(charged) == 1
    assert len(final.approval_ids) == len(set(final.approval_ids.values()))
    assert "build" in final.approval_ids


class _FakePaperScanner:
    """A deterministic stand-in for the real Tavily feed — hermetic, but the
    exact output shape TavilyScanner returns."""

    def scan(self, problem: str, uim):
        return {
            "papers_scanned": 2,
            "matches": [
                {"title": "A Survey of Sovereign Mesh Networks", "url": "https://arxiv.org/abs/1", "content": "...", "score": 0.9},
                {"title": "Local-First Agent Architectures", "url": "https://arxiv.org/abs/2", "content": "...", "score": 0.8},
            ],
            "candidates": ["A Survey of Sovereign Mesh Networks", "Local-First Agent Architectures", "fallback pred"],
            "notes": "tavily: 2 paper(s) on 'mesh arxiv paper'",
        }


def test_real_scan_feeds_surface_stage(env, rebuild) -> None:
    """Phase 2b: the scanner's real papers land in the persisted scan
    artifact, and the surface stage surfaces them as next problems."""
    engine = rebuild(scanner=_FakePaperScanner())
    turn = engine.start("Mesh for local-first agents", charger="stub")
    turn = _drive_with_approvals(engine, turn.turn_id)
    assert turn.status == "DONE"

    # scan note reports the real count; the artifact persists the evidence
    assert any(n.startswith("scan: 2 papers (tavily:") for n in turn.notes)
    scan_path = env["tmp_path"] / "rt" / "scans" / f"{turn.turn_id}.json"
    assert scan_path.exists()
    scan = json.loads(scan_path.read_text())
    assert scan["papers_scanned"] == 2
    assert len(scan["matches"]) == 2
    assert scan["candidates"][0] == "A Survey of Sovereign Mesh Networks"  # papers lead
    # the surface stage consumed those candidates
    assert any("candidate(s) surfaced" in n for n in turn.notes)


def test_engine_defaults_to_real_scanner(env, rebuild) -> None:
    """Phase 2b: with no scanner injected, the engine wires the real Tavily
    feed (it degrades to 0 papers offline — never fabricates)."""
    from msb_v3.flywheel.chargers import TavilyScanner

    engine = rebuild(scanner=None)
    assert isinstance(engine._scanner, TavilyScanner)


def test_surface_falls_back_to_uim_when_scan_missing(env, rebuild) -> None:
    """A pre-2b turn (no scan artifact persisted) still surfaces candidates:
    the fallback reads the UIM's own predictions — the loop never stalls on
    a missing scan."""
    engine = rebuild()
    turn = engine.start("Pre-2b turn without a scan", charger="stub")
    turn = _drive_with_approvals(engine, turn.turn_id)
    assert turn.status == "DONE"

    # simulate a pre-2b turn: remove the persisted scan artifact, then re-run
    # the surface stage — candidates must come from the UIM, not the scan.
    scan_path = env["tmp_path"] / "rt" / "scans" / f"{turn.turn_id}.json"
    assert scan_path.exists()
    scan_path.unlink()
    engine._stage_surface_problems(turn)
    assert turn.notes[-1] == "next problems: 3 candidate(s) surfaced"
