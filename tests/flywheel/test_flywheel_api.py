"""/flywheel API tests — start a turn, park at approvals, approve to DONE."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import msb_v3.api.flywheel as flywheel_api
from msb_v3.api.app import create_app
from msb_v3.flywheel.engine import FlywheelEngine
from msb_v3.governance.approval import ApprovalQueue
from msb_v3.governance.budget import BudgetLedger
from msb_v3.governance.governor import OuroborosGovernor
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.uac.audit_chain import AuditChain
from msb_v3.uac.axiom_library import AxiomLibrary


@pytest.fixture()
def client(tmp_path, monkeypatch):
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    queue = ApprovalQueue(db_path=str(tmp_path / "appr.db"), audit_chain=chain)
    ledger = BudgetLedger(
        db_path=str(tmp_path / "budget.db"),
        limits={"research_calls": 10, "tokens": 1000, "iterations": 50},
        window_s=3600,
    )
    switch = KillSwitch(db_path=str(tmp_path / "ks.db"), audit_chain=chain)
    governor = OuroborosGovernor(db_path=str(tmp_path / "gov.db"))
    engine = FlywheelEngine(
        db_path=str(tmp_path / "turns.db"),
        queue=queue, ledger=ledger, switch=switch, governor=governor,
        audit_chain=chain,
        axiom_library=AxiomLibrary(db_path=str(tmp_path / "axiom.db")),
        vault_root=tmp_path / "vault",
        runtime_root=tmp_path / "rt",
    )
    monkeypatch.setattr(flywheel_api, "_engine", engine)
    return TestClient(create_app()), engine


def _parked(client, turn_id: str):
    """Poll until the turn parks or completes (background task runs fast
    with the stub charger)."""
    for _ in range(20):
        r = client.get(f"/flywheel/turns/{turn_id}")
        assert r.status_code == 200
        turn = r.json()
        if turn["status"] in ("WAITING_APPROVAL", "DONE", "HALTED", "ERROR", "ALREADY_EXISTS"):
            return turn
        import time

        time.sleep(0.05)
    raise AssertionError("turn did not reach a terminal/parked state")


def test_start_turn_and_approve_to_done(client) -> None:
    client, _engine = client
    r = client.post(
        "/flywheel/turn",
        json={"problem": "API-driven flywheel turn", "charger": "stub"},
    )
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] is True
    turn_id = body["turn"]["turn_id"]

    turn = _parked(client, turn_id)
    assert turn["status"] == "WAITING_APPROVAL"
    assert "build" in turn["approval_ids"]

    for _ in range(3):  # approve build/combine/record
        r = client.post(f"/flywheel/turns/{turn_id}/approve", json={"operator": "wilson"})
        assert r.status_code == 200
        turn = r.json()
        if turn["status"] == "DONE":
            break
    assert turn["status"] == "DONE"
    assert turn["record_path"] is not None


def test_turn_list_and_validation(client) -> None:
    client, _engine = client
    assert client.get("/flywheel/turns").status_code == 200
    assert client.post("/flywheel/turn", json={"problem": ""}).status_code == 422
    assert client.post(
        "/flywheel/turn", json={"problem": "x", "charger": "bogus"}
    ).status_code == 422
    assert client.get("/flywheel/turns/nope").status_code == 404
