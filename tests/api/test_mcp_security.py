import json

import pytest
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app
from msb_v3.api import mcp_bridge


@pytest.fixture()
def client():
    return TestClient(create_app())


SECRET = "secret-token"


def _post(client: TestClient, payload: dict[str, object]):
    return client.post(
        "/mcp/proxy",
        json=payload,
        headers={"x-mcp-secret": SECRET},
    )


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    monkeypatch.setattr(mcp_bridge, "_MCP_BRIDGE_SECRET", SECRET, raising=False)


def test_missing_secret_returns_401(client):
    response = client.post("/mcp/proxy", json={"tool": "status", "args": {}})
    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"


def test_invalid_secret_returns_401(client):
    response = client.post(
        "/mcp/proxy",
        json={"tool": "status", "args": {}},
        headers={"x-mcp-secret": "wrong"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"


def test_path_traversal_is_rejected(client):
    response = _post(client, {"tool": "vault_read", "args": {"path": "../../etc/passwd"}})
    assert response.status_code == 400
    assert response.json()["detail"] == "path traversal detected"


def test_vault_write_normalizes_path(client, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setattr(mcp_bridge, "_VAULT_BASE", root.resolve(), raising=False)

    response = _post(client, {"tool": "vault_write", "args": {"path": "notes/test.md", "content": "ok"}})
    assert response.status_code == 200
    assert (root / "notes" / "test.md").read_text() == "ok"


def test_vault_read_requires_path_traversal_protection(client, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setattr(mcp_bridge, "_VAULT_BASE", root.resolve(), raising=False)

    response = _post(client, {"tool": "vault_read", "args": {"path": "../secret.txt"}})
    assert response.status_code == 400


def test_mcp_audit_log_is_emitted(client, caplog):
    caplog.set_level("INFO", logger="msb_v3.mcp_audit")
    response = _post(client, {"tool": "status", "args": {}})
    assert response.status_code in {200, 502}
    events = [rec.message for rec in caplog.records if rec.name == "msb_v3.mcp_audit"]
    assert any('"action": "tool:status"' in message for message in events)
    assert any('"result": "success"' in message or '"result": "upstream_error' in message for message in events)
