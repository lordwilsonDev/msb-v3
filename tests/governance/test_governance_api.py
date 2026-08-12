"""/governance API tests — status, budget, kill switch, approvals, drill."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import msb_v3.api.governance as gov_api
from msb_v3.api.app import create_app
from msb_v3.governance.approval import ApprovalQueue
from msb_v3.governance.budget import BudgetLedger
from msb_v3.governance.governor import OuroborosGovernor
from msb_v3.governance.guard import Guard
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.uac.audit_chain import AuditChain


@pytest.fixture()
def client(tmp_path, monkeypatch):
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    ledger = BudgetLedger(
        db_path=str(tmp_path / "budget.db"),
        limits={"research_calls": 1, "tokens": 100, "iterations": 10},
    )
    queue = ApprovalQueue(db_path=str(tmp_path / "appr.db"), audit_chain=chain)
    switch = KillSwitch(db_path=str(tmp_path / "ks.db"), audit_chain=chain)
    governor = OuroborosGovernor(db_path=str(tmp_path / "gov.db"))
    monkeypatch.setattr(gov_api, "_ledger", ledger)
    monkeypatch.setattr(gov_api, "_queue", queue)
    monkeypatch.setattr(gov_api, "_switch", switch)
    monkeypatch.setattr(gov_api, "_governor", governor)
    monkeypatch.setattr(gov_api, "_audit", chain)
    monkeypatch.setattr(gov_api, "_guard", Guard(switch, ledger, queue, governor, audit_chain=chain))
    return TestClient(create_app())


def test_status_shape(client: TestClient) -> None:
    r = client.get("/governance/status")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"killswitch", "budgets", "governor", "approvals"}
    assert body["killswitch"]["armed"] is False
    assert body["approvals"]["pending"] == 0


def test_approval_flow_over_http(client: TestClient) -> None:
    r = client.post(
        "/governance/approvals",
        json={"kind": "build", "title": "stage 7", "payload": {"m": 1}, "evidence_refs": ["uim.json"]},
    )
    assert r.status_code == 201
    item = r.json()
    assert item["status"] == "PENDING"

    r = client.get("/governance/approvals")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 1

    r = client.post(f"/governance/approvals/{item['id']}/approve", json={"operator": "wilson"})
    assert r.status_code == 200
    assert r.json()["status"] == "APPROVED"

    r = client.get("/governance/status")
    assert r.json()["approvals"]["pending"] == 0


def test_double_approve_conflicts(client: TestClient) -> None:
    item = client.post(
        "/governance/approvals", json={"kind": "build", "title": "t"},
    ).json()
    assert client.post(f"/governance/approvals/{item['id']}/approve", json={}).status_code == 200
    assert client.post(f"/governance/approvals/{item['id']}/approve", json={}).status_code == 409


def test_unknown_kind_422(client: TestClient) -> None:
    r = client.post("/governance/approvals", json={"kind": "explode", "title": "t"})
    assert r.status_code == 422


def test_killswitch_flow_over_http(client: TestClient) -> None:
    r = client.post("/governance/killswitch/arm", json={"operator": "wilson", "reason": "drill"})
    assert r.status_code == 200
    assert r.json()["armed"] is True
    r = client.post("/governance/killswitch/disarm", json={"operator": "wilson"})
    assert r.json()["armed"] is False


def test_budget_reset(client: TestClient) -> None:
    r = client.post("/governance/budget/reset", json={"category": "research_calls"})
    assert r.status_code == 200
    assert r.json()["reset"] is True
    assert client.post("/governance/budget/reset", json={}).status_code == 200


def test_check_drill_budget_halt(client: TestClient) -> None:
    # Exhaust research_calls (limit 1) directly on the patched ledger.
    gov_api._ledger.spend("research_calls")
    r = client.post(
        "/governance/check",
        json={"action": "research", "budget_units": {"research_calls": 1}},
    )
    assert r.status_code == 200
    assert r.json()["allowed"] is False
    assert r.json()["action"] == "HALT"


def test_check_drill_approval_required(client: TestClient) -> None:
    r = client.post("/governance/check", json={"action": "build", "kind": "build"})
    assert r.status_code == 200
    assert r.json()["allowed"] is False
    assert r.json()["action"] == "APPROVAL_REQUIRED"


def test_check_drill_all_clear(client: TestClient) -> None:
    r = client.post("/governance/check", json={"action": "charge"})
    assert r.status_code == 200
    assert r.json()["allowed"] is True
    assert r.json()["action"] == "OK"
