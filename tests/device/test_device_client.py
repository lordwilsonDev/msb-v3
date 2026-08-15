"""Hermetic tests for the signed-device CLI client (msb_v3.device.client).

The server is mocked with ``httpx.MockTransport`` (same convention as
tests/vesta/test_dev_harness.py); the mock handlers cryptographically verify
every signature the client produces, so these tests prove the client signs
the right bytes without needing a live server or the pairing code.
"""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from msb_v3.device.client import DeviceClient, DeviceClientError, main
from msb_v3.node.crypto import verify
from msb_v3.node.protocol import b64decode, b64encode, canonical_json, request_signature_payload, session_signature_payload


def _new_state() -> dict[str, Any]:
    return {
        "challenge": "challenge-fixture",
        "session_id": "session-fixture",
        "device_id": None,
        "public_key": None,
        "challenge_calls": 0,
    }


def _enroll_branch(state: dict[str, Any], request: httpx.Request) -> httpx.Response | None:
    """Serve /node/v1/auth/enroll (capturing the device key), or None to let
    the caller handle other paths."""
    if request.url.path == "/node/v1/auth/enroll":
        body = json.loads(request.read())
        state["device_id"] = body["device_id"]
        state["public_key"] = b64decode(body["public_key"])
        return httpx.Response(200, json={"device_id": body["device_id"], "status": "ACTIVE"}, request=request)
    return None


def _mock_client(tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> DeviceClient:
    transport = httpx.MockTransport(handler)
    client = DeviceClient(
        base_url="http://127.0.0.1:8766",
        state_dir=tmp_path / "state",
        pairing_code=kwargs.pop("pairing_code", "pairing"),
        operator_token=kwargs.pop("operator_token", "op-token"),
        **kwargs,
    )
    client._client = httpx.Client(transport=transport, base_url=client.base_url)
    return client


def _envelope_of(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k != "signature"}


def _assert_envelope_signature(public_key: bytes, body: dict[str, Any]) -> None:
    unsigned = _envelope_of(body)
    expected = request_signature_payload(
        unsigned["request_id"],
        unsigned["session_id"],
        unsigned["timestamp"],
        unsigned["nonce"],
        unsigned["intent"],
    )
    assert unsigned == expected
    assert verify(public_key, canonical_json(expected), b64decode(body["signature"]))


# ── Identity persistence ────────────────────────────────────────────────────
def test_enroll_persists_identity_with_0600_permissions(tmp_path: Path) -> None:
    state: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        state["device_id"] = body["device_id"]
        state["public_key"] = b64decode(body["public_key"])
        return httpx.Response(
            200,
            json={"device_id": body["device_id"], "status": "ACTIVE", "hardware_assurance": body["hardware_assurance"]},
            request=request,
        )

    client = _mock_client(tmp_path, handler)
    result = client.enroll(device_id="my-phone")
    assert result["device_id"] == "my-phone"

    device_file = client.device_file
    assert device_file.exists()
    mode = stat.S_IMODE(device_file.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"

    identity = json.loads(device_file.read_text())
    assert identity["device_id"] == "my-phone"
    assert identity["public_key"] == b64encode(state["public_key"])

    # re-enroll without --force is a no-op, not a new identity
    again = client.enroll()
    assert again["device_id"] == "my-phone"
    assert "already enrolled" in again["status"]


def test_enroll_requires_pairing_code(tmp_path: Path) -> None:
    client = _mock_client(tmp_path, lambda r: httpx.Response(404, request=r), pairing_code="")
    with pytest.raises(DeviceClientError, match="is empty"):
        client.enroll()


# ── Session ─────────────────────────────────────────────────────────────────
def _auth_handlers(state: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/node/v1/auth/challenge":
            state["challenge_calls"] += 1
            return httpx.Response(200, json={"device_id": state["device_id"], "challenge": state["challenge"]}, request=request)
        if request.url.path == "/node/v1/auth/session":
            body = json.loads(request.read())
            signed = canonical_json(session_signature_payload(body["device_id"], body["challenge"]))
            assert verify(state["public_key"], signed, b64decode(body["signature"]))
            return httpx.Response(
                200,
                json={"session_id": state["session_id"], "device_id": state["device_id"], "expires_at": "2099-01-01T00:00:00+00:00"},
                request=request,
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    return handler


def test_enroll_then_signed_session_persisted_and_reused(tmp_path: Path) -> None:
    state = _new_state()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/node/v1/auth/enroll":
            body = json.loads(request.read())
            state["device_id"] = body["device_id"]
            state["public_key"] = b64decode(body["public_key"])
            return httpx.Response(200, json={"device_id": body["device_id"], "status": "ACTIVE"}, request=request)
        return _auth_handlers(state)(request)

    client = _mock_client(tmp_path, handler)
    client.enroll()
    session_id = client.ensure_session()
    assert session_id == "session-fixture"
    assert state["challenge_calls"] == 1

    # session.json persisted and reused — no second challenge issued
    session_file = client.session_file
    assert session_file.exists()
    assert stat.S_IMODE(session_file.stat().st_mode) == 0o600
    assert client.ensure_session() == "session-fixture"
    assert state["challenge_calls"] == 1


# ── Chat ────────────────────────────────────────────────────────────────────
def test_chat_sends_verified_signed_envelope(tmp_path: Path) -> None:
    state = _new_state()

    def handler(request: httpx.Request) -> httpx.Response:
        if (enrolled := _enroll_branch(state, request)) is not None:
            return enrolled
        if request.url.path == "/node/v1/auth/challenge":
            state["challenge_calls"] += 1
            return httpx.Response(200, json={"device_id": state["device_id"], "challenge": state["challenge"]}, request=request)
        if request.url.path == "/node/v1/auth/session":
            return httpx.Response(
                200,
                json={"session_id": state["session_id"], "expires_at": "2099-01-01T00:00:00+00:00"},
                request=request,
            )
        if request.url.path == "/vesta/signed-chat":
            body = json.loads(request.read())
            _assert_envelope_signature(state["public_key"], body)
            assert body["intent"]["type"] == "chat"
            assert body["intent"]["target"]["query"] == "hello"
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "decision": "ALLOW",
                    "policy_version": "vesta-policy-1",
                    "payload": {"query": "hello", "text": "LOOPBACK_OK", "model": "fixture"},
                    "error": None,
                    "audit_event_ids": [1],
                },
                request=request,
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    client = _mock_client(tmp_path, handler)
    client.enroll()
    result = client.chat("hello")
    assert result["decision"] == "ALLOW"
    assert result["payload"]["text"] == "LOOPBACK_OK"


# ── FILE_READ ───────────────────────────────────────────────────────────────
def test_read_sends_signed_engage_request(tmp_path: Path) -> None:
    state = _new_state()

    def handler(request: httpx.Request) -> httpx.Response:
        if (enrolled := _enroll_branch(state, request)) is not None:
            return enrolled
        if request.url.path == "/node/v1/auth/challenge":
            return httpx.Response(200, json={"device_id": state["device_id"], "challenge": state["challenge"]}, request=request)
        if request.url.path == "/node/v1/auth/session":
            return httpx.Response(200, json={"session_id": state["session_id"], "expires_at": "2099-01-01T00:00:00+00:00"}, request=request)
        if request.url.path == "/node/v1/engage":
            body = json.loads(request.read())
            _assert_envelope_signature(state["public_key"], body)
            assert body["intent"]["type"] == "read_file"
            assert body["intent"]["target"]["path"] == "notes.md"
            return httpx.Response(200, json={"path": "notes.md", "content": "secret note", "sha256": "0" * 64}, request=request)
        raise AssertionError(f"unexpected path {request.url.path}")

    client = _mock_client(tmp_path, handler)
    client.enroll()
    result = client.read("notes.md")
    assert result["content"] == "secret note"


# ── FILE_WRITE (operator submit + device signed-approve) ────────────────────
def test_write_operator_submit_then_device_signed_approval(tmp_path: Path) -> None:
    state = _new_state()
    content = "# hello\n"
    payload_sha = hashlib.sha256(content.encode()).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if (enrolled := _enroll_branch(state, request)) is not None:
            return enrolled
        if request.url.path == "/node/v1/auth/challenge":
            return httpx.Response(200, json={"device_id": state["device_id"], "challenge": state["challenge"]}, request=request)
        if request.url.path == "/node/v1/auth/session":
            return httpx.Response(200, json={"session_id": state["session_id"], "expires_at": "2099-01-01T00:00:00+00:00"}, request=request)
        if request.url.path == "/vesta/execute":
            body = json.loads(request.read())
            assert request.headers["Authorization"] == "Bearer op-token"
            assert body["session"] == state["session_id"]
            assert body["path"] == "notes.md"
            assert body["content"] == content
            return httpx.Response(
                200,
                json={
                    "approval_id": "ack_abc",
                    "task_id": "task_abc",
                    "target_path": body["path"],
                    "payload_sha256": payload_sha,
                    "policy_version": "vesta-policy-1",
                    "status": "approval_required",
                    "decision": "REQUIRE_APPROVAL",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "evidence_refs": ["ev_1"],
                },
                request=request,
            )
        if request.url.path == "/vesta/approvals/ack_abc/signed-approve":
            body = json.loads(request.read())
            _assert_envelope_signature(state["public_key"], body)
            target = body["intent"]["target"]
            assert target["approval_id"] == "ack_abc"
            assert target["target_path"] == "notes.md"
            assert target["payload_sha256"] == payload_sha
            assert target["policy_version"] == "vesta-policy-1"
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "receipt": {"path": "notes.md", "existed": False, "after_sha256": payload_sha, "size": len(content)},
                    "audit_event_ids": [1, 2],
                },
                request=request,
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    client = _mock_client(tmp_path, handler)
    client.enroll()
    result = client.write("notes.md", content)
    assert result["status"] == "completed"
    assert result["receipt"]["after_sha256"] == payload_sha


def test_write_requires_operator_token(tmp_path: Path) -> None:
    state = _new_state()
    client = _mock_client(tmp_path, lambda r: _enroll_branch(state, r) or httpx.Response(404, request=r), operator_token="")
    client.enroll()
    with pytest.raises(DeviceClientError, match="MSB_OPERATOR_TOKEN"):
        client.write("notes.md", "hi")


# ── SHELL_EXEC (operator submit + device signed-approve) ────────────────────
def test_shell_operator_submit_then_device_signed_approval(tmp_path: Path) -> None:
    state = _new_state()
    command_sha = "c0ffee" * 10

    def handler(request: httpx.Request) -> httpx.Response:
        if (enrolled := _enroll_branch(state, request)) is not None:
            return enrolled
        if request.url.path == "/node/v1/auth/challenge":
            return httpx.Response(200, json={"device_id": state["device_id"], "challenge": state["challenge"]}, request=request)
        if request.url.path == "/node/v1/auth/session":
            return httpx.Response(200, json={"session_id": state["session_id"], "expires_at": "2099-01-01T00:00:00+00:00"}, request=request)
        if request.url.path == "/vesta/shell/execute":
            body = json.loads(request.read())
            assert request.headers["Authorization"] == "Bearer op-token"
            assert body["executable"] == "echo"
            assert body["args"] == ["hello"]
            return httpx.Response(
                200,
                json={
                    "approval_id": "shell_ack_xyz",
                    "task_id": "task_xyz",
                    "command_sha256": command_sha,
                    "command": {"executable": "echo", "args": ["hello"]},
                    "policy_version": "vesta-policy-1",
                    "status": "approval_required",
                    "decision": "REQUIRE_APPROVAL",
                },
                request=request,
            )
        if request.url.path == "/vesta/shell/approvals/shell_ack_xyz/signed-approve":
            body = json.loads(request.read())
            _assert_envelope_signature(state["public_key"], body)
            target = body["intent"]["target"]
            assert target["approval_id"] == "shell_ack_xyz"
            assert target["command_sha256"] == command_sha
            assert target["policy_version"] == "vesta-policy-1"
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "execution": {"executable": "echo", "returncode": 0, "stdout": "hello\n", "stderr": "", "timed_out": False, "output_truncated": False},
                    "verification": {"ok": True},
                    "audit_event_ids": [3, 4],
                },
                request=request,
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    client = _mock_client(tmp_path, handler)
    client.enroll()
    result = client.shell("echo", ["hello"])
    assert result["status"] == "completed"
    assert result["verification"]["ok"] is True
    assert result["execution"]["stdout"] == "hello\n"


# ── CLI error paths ─────────────────────────────────────────────────────────
def test_cli_enroll_exits_2_without_pairing_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSB_NODE_PAIRING_CODE", raising=False)
    assert main(["enroll"]) == 2


def test_cli_operation_exits_2_without_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSB_NODE_PAIRING_CODE", raising=False)
    assert main(["chat", "hi", "--state-dir", str(tmp_path / "state")]) == 2


def test_cli_accepts_global_flags_after_subcommand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: --json (and --url/--state-dir) must work after the
    subcommand, e.g. `device-client chat hi --json`."""
    monkeypatch.delenv("MSB_NODE_PAIRING_CODE", raising=False)
    # no identity + no pairing -> exits 2 via the config path, proving the
    # flags parsed fine after the subcommand (a parse error would exit 1/2
    # with a usage message instead of reaching the config check).
    assert main(["chat", "hi", "--json", "--state-dir", str(tmp_path / "state")]) == 2
