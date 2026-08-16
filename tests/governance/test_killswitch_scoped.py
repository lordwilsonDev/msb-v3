"""Scoped kill switch (unified-architecture §13, forensic finding 2026-08-15).

STOP agent_07 must not mean STOP entire MSB; DISABLE shell_execute must not
disable vault_search. Global arm still blocks everyone; scopes never loosen
a global lockdown.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import msb_v3.api.governance as gov_api
from msb_v3.agent.safety import ActionGate
from msb_v3.api.app import create_app
from msb_v3.core.config import settings
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.uac.audit_chain import AuditChain


@pytest.fixture()
def switch(tmp_path):
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    return KillSwitch(db_path=str(tmp_path / "ks.db"), audit_chain=chain)


# --- unit: scoped semantics -------------------------------------------------


def test_scope_arm_blocks_only_its_scope(switch):
    switch.arm_scope("tool", "shell_execute", "wilson", "disable shell for a bit")
    assert switch.is_blocked("tool", "shell_execute") is True
    # unrelated scopes keep running
    assert switch.is_blocked("tool", "vault_search") is False
    assert switch.is_blocked("agent", "agent_07") is False
    # global brake untouched
    assert switch.is_armed() is False


def test_scope_arm_does_not_touch_global(switch):
    switch.arm_scope("agent", "agent_07", "wilson")
    assert switch.is_armed() is False
    switch.arm("wilson", "emergency")
    assert switch.is_armed() is True
    # global arm blocks even scopes that were never scoped
    assert switch.is_blocked("agent", "agent_99") is True
    switch.disarm("wilson")
    # scoped arm survives the global disarm
    assert switch.is_blocked("agent", "agent_07") is True


def test_scope_disarm(switch):
    switch.arm_scope("tenant", "acme", "wilson")
    assert switch.is_blocked("tenant", "acme") is True
    switch.disarm_scope("tenant", "acme", "wilson")
    assert switch.is_blocked("tenant", "acme") is False


def test_unknown_scope_type_rejected(switch):
    with pytest.raises(ValueError):
        switch.arm_scope("galaxy", "milky-way", "wilson")


def test_state_lists_armed_scopes(switch):
    switch.arm_scope("tool", "shell_execute", "wilson")
    switch.arm_scope("agent", "agent_07", "wilson")
    scopes = switch.state()["scopes"]
    armed = {(s["scope_type"], s["scope_id"]) for s in scopes}
    assert ("tool", "shell_execute") in armed
    assert ("agent", "agent_07") in armed
    assert switch.state()["armed"] is False


def test_require_allowed_scoped(switch):
    switch.arm_scope("tool", "shell_execute", "wilson")
    switch.require_allowed("tool", "vault_search")  # no raise
    with pytest.raises(Exception):
        switch.require_allowed("tool", "shell_execute")


# --- ActionGate integration --------------------------------------------------


def test_action_gate_blocks_scoped_tool_only(switch):
    gate = ActionGate(killswitch=switch)
    switch.arm_scope("tool", "write_file", "wilson")
    blocked = gate.gate("write_file")
    assert blocked.action == "BLOCK"
    assert "tool scope" in blocked.reason
    # other capabilities unaffected
    safe = gate.gate("read_vault")
    assert safe.action == "SAFE"


def test_action_gate_blocks_scoped_agent(switch):
    gate = ActionGate(killswitch=switch)
    switch.arm_scope("agent", "agent_07", "wilson")
    assert gate.gate("read_vault", agent_id="agent_07").action == "BLOCK"
    assert gate.gate("read_vault", agent_id="agent_08").action == "SAFE"


# --- API surface -------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    switch = KillSwitch(db_path=str(tmp_path / "ks.db"), audit_chain=chain)
    monkeypatch.setattr(gov_api, "_switch", switch)
    monkeypatch.setattr(settings, "operator_token", "test-operator-token")
    return TestClient(create_app(), headers={"Authorization": "Bearer test-operator-token"})


def test_scope_endpoints_over_http(client):
    r = client.post(
        "/governance/killswitch/scope/arm",
        json={"scope_type": "tool", "scope_id": "shell_execute", "operator": "wilson", "reason": "drill"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["armed"] is True
    assert body["scope_type"] == "tool"

    r = client.post("/governance/killswitch/scope/disarm", json={"scope_type": "tool", "scope_id": "shell_execute"})
    assert r.status_code == 200
    assert r.json()["armed"] is False

    # global status still reports armed=False with the scope gone
    status = client.get("/governance/status").json()
    assert status["killswitch"]["armed"] is False


def test_scope_endpoint_rejects_unknown_type(client):
    r = client.post(
        "/governance/killswitch/scope/arm",
        json={"scope_type": "galaxy", "scope_id": "x"},
    )
    assert r.status_code == 422


def test_scope_endpoints_require_operator(client, monkeypatch):
    monkeypatch.setattr(settings, "operator_token", "")
    r = client.post(
        "/governance/killswitch/scope/arm",
        json={"scope_type": "tool", "scope_id": "shell_execute"},
    )
    assert r.status_code == 503
