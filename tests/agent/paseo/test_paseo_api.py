"""/agent/paseo API tests — operator gating, validation, permission decisions.

Every paseo endpoint is operator-gated (state-changing: worktrees, external
agents, repo mutation). Permission decisions are durable Vesta approvals —
the respond endpoint is tested end-to-end against a temp store; the
daemon-facing create/send/interrupt endpoints are validated for their
fail-closed 422s (no daemon required — the adapter is never constructed
before validation passes).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app
from msb_v3.core.config import settings
from msb_v3.vesta.approvals import VestaApprovalStore


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "operator_token", "test-operator-token")
    monkeypatch.setattr(settings, "vesta_task_db_path", str(tmp_path / "vesta" / "tasks.db"))
    return TestClient(create_app(), headers={"Authorization": "Bearer test-operator-token"})


def test_paseo_endpoints_require_operator(monkeypatch):
    monkeypatch.setattr(settings, "operator_token", "test-operator-token")
    unauth = TestClient(create_app())
    assert unauth.get("/agent/paseo/status/a1").status_code == 401
    assert unauth.post("/agent/paseo/create", json={}).status_code == 401


def test_providers_include_paseo(client):
    body = client.get("/agent/providers").json()
    ids = {p["provider_id"] for p in body["providers"]}
    assert {"paseo.claude", "paseo.codex", "paseo.opencode"} <= ids


def test_create_requires_cwd_and_title(client):
    assert client.post("/agent/paseo/create", json={}).status_code == 422
    assert client.post("/agent/paseo/create", json={"cwd": "/tmp"}).status_code == 422


def test_send_requires_agent_and_prompt(client):
    assert client.post("/agent/paseo/send", json={}).status_code == 422
    assert client.post("/agent/paseo/send", json={"agent_id": "a1"}).status_code == 422


def test_interrupt_requires_agent(client):
    assert client.post("/agent/paseo/interrupt", json={}).status_code == 422


def test_respond_requires_boolean(client):
    assert client.post("/agent/paseo/permissions/x/respond", json={}).status_code == 422
    assert client.post("/agent/paseo/permissions/x/respond", json={"approved": "yes"}).status_code == 422


def test_permission_respond_approve_and_deny(client, tmp_path):
    # Park a permission request in the same (temp) store the endpoint uses.
    def park(agent_id, request_id):
        async def run():
            from msb_v3.agent.paseo.permissions import PaseoPermissionBroker

            broker = PaseoPermissionBroker()
            return await broker.register(
                agent_id,
                "task-1",
                {"id": request_id, "provider": "claude", "name": "Write file", "kind": "tool"},
            )

        return asyncio.run(run())

    store = VestaApprovalStore(db_path=str(tmp_path / "vesta" / "tasks.db"))
    approval = park("agent-1", "perm-1")

    resp = client.post(f"/agent/paseo/permissions/{approval['approval_id']}/respond", json={"approved": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["approval"]["status"] == "APPROVED"
    assert store.get(approval["approval_id"])["decided_by"] == "operator"

    approval2 = park("agent-2", "perm-2")
    resp2 = client.post(f"/agent/paseo/permissions/{approval2['approval_id']}/respond", json={"approved": False, "message": "no"})
    assert resp2.status_code == 200
    assert resp2.json()["approval"]["status"] == "REJECTED"


def test_activity_endpoint_returns_curated_timeline(client, monkeypatch):
    class _FakeAdapter:
        async def activity(self, agent_id, *, limit=None):
            return {"update_count": 4, "mode": "default", "content": "Showing 4 activities\n- edited"}

    import msb_v3.api.agent as agent_api

    monkeypatch.setattr(agent_api, "_paseo_adapter", lambda: _FakeAdapter())
    resp = client.get("/agent/paseo/activity/a1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["update_count"] == 4
    assert "edited" in body["content"]


def test_activity_endpoint_503_when_daemon_down(client, monkeypatch):
    class _FakeAdapter:
        async def activity(self, agent_id, *, limit=None):
            from msb_v3.agent.paseo import PaseoMcpError

            raise PaseoMcpError("cannot reach Paseo daemon")

    import msb_v3.api.agent as agent_api

    monkeypatch.setattr(agent_api, "_paseo_adapter", lambda: _FakeAdapter())
    resp = client.get("/agent/paseo/activity/a1")
    assert resp.status_code == 503
    assert "unreachable" in resp.json()["detail"]


def test_permission_pending_listing(client, tmp_path):
    def park():
        async def run():
            from msb_v3.agent.paseo.permissions import PaseoPermissionBroker

            broker = PaseoPermissionBroker()
            return await broker.register(
                "agent-1",
                "task-1",
                {"id": "perm-1", "provider": "claude", "name": "Run command", "kind": "tool"},
            )

        return asyncio.run(run())

    park()
    body = client.get("/agent/paseo/permissions").json()
    assert body["ok"] is True
    assert body["count"] >= 1
    assert any("paseo.agent-1.perm-1" == p["bind_id"] for p in body["permissions"])
