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


def test_verify_build_with_real_files_returns_verified(client, tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(mcp_bridge, "_VAULT_BASE", vault_root.resolve(), raising=False)
    echo_dir = tmp_path / "echo"
    monkeypatch.setattr(mcp_bridge, "_VERIFY_BUILD_ECHO_DIR", echo_dir, raising=False)

    real_file = tmp_path / "thing.py"
    real_file.write_text("# real\n")
    real_test = tmp_path / "test_thing.py"
    real_test.write_text("# real test\n")

    response = _post(client, {
        "tool": "verify_build",
        "args": {"id": "my-build", "files": [str(real_file)], "tests": [str(real_test)]},
    })
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["status"] == "VERIFIED"

    echo_path = echo_dir / "my-build.txt"
    assert echo_path.exists()
    echo_content = echo_path.read_text()
    assert "VERIFIED" in echo_content
    assert str(real_file) in echo_content

    vault_note = vault_root / "40_Memory" / "Verified-Builds-Log.md"
    assert vault_note.exists()
    assert "my-build" in vault_note.read_text()


def test_verify_build_with_missing_file_returns_failed(client, tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(mcp_bridge, "_VAULT_BASE", vault_root.resolve(), raising=False)
    echo_dir = tmp_path / "echo"
    monkeypatch.setattr(mcp_bridge, "_VERIFY_BUILD_ECHO_DIR", echo_dir, raising=False)

    missing = tmp_path / "does_not_exist.py"

    response = _post(client, {
        "tool": "verify_build",
        "args": {"id": "bad-build", "files": [str(missing)]},
    })
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["status"] == "FAILED"
    assert str(missing) in result["missing_files"]

    assert not (echo_dir / "bad-build.txt").exists()
    vault_note = vault_root / "40_Memory" / "Verified-Builds-Log.md"
    assert not vault_note.exists()


def test_verify_build_directory_path_is_not_a_file(client, tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(mcp_bridge, "_VAULT_BASE", vault_root.resolve(), raising=False)
    echo_dir = tmp_path / "echo"
    monkeypatch.setattr(mcp_bridge, "_VERIFY_BUILD_ECHO_DIR", echo_dir, raising=False)

    a_directory = tmp_path / "some_dir"
    a_directory.mkdir()

    response = _post(client, {
        "tool": "verify_build",
        "args": {"id": "dir-build", "files": [str(a_directory)]},
    })
    result = response.json()["result"]
    assert result["status"] == "FAILED"
    assert str(a_directory) in result["missing_files"]


def test_verify_build_accepts_comma_separated_string_files(client, tmp_path, monkeypatch):
    # mcp_adapter.py's tools/list schema declares every argument as a plain
    # string (it has no per-tool type info), so a real MCP tool call sends
    # files/tests as a comma-separated string, not a JSON array. Only the
    # raw HTTP path (e.g. curl with a hand-built JSON body) can send a real
    # list. Both must work.
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(mcp_bridge, "_VAULT_BASE", vault_root.resolve(), raising=False)
    echo_dir = tmp_path / "echo"
    monkeypatch.setattr(mcp_bridge, "_VERIFY_BUILD_ECHO_DIR", echo_dir, raising=False)

    real_file = tmp_path / "thing.py"
    real_file.write_text("# real\n")
    real_test = tmp_path / "test_thing.py"
    real_test.write_text("# real test\n")

    response = _post(client, {
        "tool": "verify_build",
        "args": {
            "id": "comma-build",
            "files": f"{real_file}, {real_test}",
        },
    })
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["status"] == "VERIFIED"

    echo_content = (echo_dir / "comma-build.txt").read_text()
    assert str(real_file) in echo_content
    assert str(real_test) in echo_content


def test_verify_build_comma_separated_string_with_missing_file_fails(client, tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(mcp_bridge, "_VAULT_BASE", vault_root.resolve(), raising=False)
    echo_dir = tmp_path / "echo"
    monkeypatch.setattr(mcp_bridge, "_VERIFY_BUILD_ECHO_DIR", echo_dir, raising=False)

    real_file = tmp_path / "thing.py"
    real_file.write_text("# real\n")
    missing = tmp_path / "missing.py"

    response = _post(client, {
        "tool": "verify_build",
        "args": {"id": "comma-fail", "files": f"{real_file},{missing}"},
    })
    result = response.json()["result"]
    assert result["status"] == "FAILED"
    assert str(missing) in result["missing_files"]
    assert str(real_file) not in result["missing_files"]


def test_verify_build_requires_id(client):
    response = _post(client, {"tool": "verify_build", "args": {"files": ["/tmp/x.py"]}})
    assert response.status_code == 400


def test_verify_build_requires_evidence_target(client):
    response = _post(client, {"tool": "verify_build", "args": {"id": "empty-build"}})
    assert response.status_code == 400


def test_verify_build_requires_auth(client):
    response = client.post(
        "/mcp/proxy",
        json={"tool": "verify_build", "args": {"id": "x", "files": ["/tmp/x"]}},
    )
    assert response.status_code == 401


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
