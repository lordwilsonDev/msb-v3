
import pytest
from fastapi.testclient import TestClient

from msb_v3 import __version__
from msb_v3.api import mcp_bridge
from msb_v3.api.app import create_app


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


def test_mcp_status_requires_auth(client):
    response = client.get("/mcp/status")
    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"


def test_mcp_status_returns_service_shape(client):
    response = client.get("/mcp/status", headers={"x-mcp-secret": SECRET})
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "msb-v3"
    assert data["version"] == __version__
    assert isinstance(data["ready"], bool)
    # Tool count must track the manifest — a stale hardcoded number would
    # silently drift from what /mcp/tools actually serves.
    assert data["tools"] == len(mcp_bridge._MCP_TOOLS)
    assert data["tools"] >= 1


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


def test_vault_rejects_sibling_directory_sharing_prefix(client, tmp_path, monkeypatch):
    """Regression: a sibling dir that merely shares the vault's name prefix
    (../vault2/...) used to pass the old string-prefix containment check.
    relative_to containment must reject it."""
    root = tmp_path / "vault"
    root.mkdir()
    sibling = tmp_path / "vault2"
    sibling.mkdir()
    (sibling / "secret.md").write_text("secret", encoding="utf-8")
    monkeypatch.setattr(mcp_bridge, "_VAULT_BASE", root.resolve(), raising=False)

    response = _post(client, {"tool": "vault_read", "args": {"path": "../vault2/secret.md"}})
    assert response.status_code == 400

    write_response = _post(client, {"tool": "vault_write", "args": {"path": "../vault2/evil.md", "content": "pwn"}})
    assert write_response.status_code == 400
    assert not (sibling / "evil.md").exists()


def test_verify_build_rejects_path_traversal_in_build_id(client, tmp_path, monkeypatch):
    """build_id is interpolated into the echo receipt filename; path
    separators and '..' must be rejected before it touches the filesystem."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(mcp_bridge, "_VAULT_BASE", vault_root.resolve(), raising=False)
    echo_dir = tmp_path / "echo"
    monkeypatch.setattr(mcp_bridge, "_VERIFY_BUILD_ECHO_DIR", echo_dir, raising=False)

    real_file = tmp_path / "thing.py"
    real_file.write_text("# real\n")

    for evil_id in ["../../../../tmp/pwn", "a/b/c", "../pwn", "x\ny"]:
        response = _post(client, {
            "tool": "verify_build",
            "args": {"id": evil_id, "files": [str(real_file)]},
        })
        assert response.status_code == 400, f"build_id {evil_id!r} should be rejected"

    assert not (echo_dir / "pwn.txt").exists()
    assert not (tmp_path / "pwn.txt").exists()


def test_verify_build_strips_control_characters_from_echo_content(client, tmp_path, monkeypatch):
    """File/test paths land verbatim in the echo receipt + vault note; newlines
    and control characters must be collapsed so they cannot inject content."""
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(mcp_bridge, "_VAULT_BASE", vault_root.resolve(), raising=False)
    echo_dir = tmp_path / "echo"
    monkeypatch.setattr(mcp_bridge, "_VERIFY_BUILD_ECHO_DIR", echo_dir, raising=False)

    # macOS/Unix filenames may legally contain a newline; verify a file
    # whose name embeds one cannot inject a fresh line into the receipt.
    injected_file = tmp_path / "thing\nINJECTED_SECRET_LINE.py"
    injected_file.write_text("# real\n")

    response = _post(client, {
        "tool": "verify_build",
        "args": {"id": "inject-build", "files": [str(injected_file)]},
    })
    assert response.status_code == 200
    assert response.json()["result"]["status"] == "VERIFIED"

    echo_content = (echo_dir / "inject-build.txt").read_text()
    # The raw newline must not survive into the file: the injected string
    # appears only in its space-collapsed form.
    assert "\nINJECTED_SECRET_LINE" not in echo_content
    assert " INJECTED_SECRET_LINE.py" in echo_content


def test_chat_and_memory_are_auth_gated_when_secret_set(monkeypatch):
    """Native /chat and /memory/* must honor the same x-mcp-secret gate as
    the MCP bridge once MCP_BRIDGE_SECRET is configured (opt-in: unset
    secret = dev mode, matching api/auth.check_auth's contract)."""
    monkeypatch.setenv("MCP_BRIDGE_SECRET", "live-secret")
    app = create_app()
    client = TestClient(app)

    assert client.post("/chat", json={"query": "hi"}).status_code == 401
    assert client.get("/memory/some-session").status_code == 401
    assert client.post("/memory/some-session", json={"role": "user", "content": "x"}).status_code == 401
    assert client.delete("/memory/some-session").status_code == 401

    # With the header the memory surface opens (chat would hit Ollama, so
    # only verify the gate itself lifts).
    ok = client.get("/memory/some-session", headers={"x-mcp-secret": "live-secret"})
    assert ok.status_code == 200


def test_mcp_audit_log_is_emitted(client, caplog):
    caplog.set_level("INFO", logger="msb_v3.mcp_audit")
    response = _post(client, {"tool": "status", "args": {}})
    assert response.status_code in {200, 502}
    events = [rec.message for rec in caplog.records if rec.name == "msb_v3.mcp_audit"]
    assert any('"action": "tool:status"' in message for message in events)
    assert any('"result": "success"' in message or '"result": "upstream_error' in message for message in events)
