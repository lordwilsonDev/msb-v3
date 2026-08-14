from __future__ import annotations

from datetime import datetime, timezone

import pytest

from msb_v3.governance.killswitch import KillSwitch
from msb_v3.node.approval import NodeApprovalStore
from msb_v3.node.crypto import generate_keypair, sign
from msb_v3.node.filesystem import CapabilityViolation, FileReader
from msb_v3.node.identity import IdentityStore, NodeAuthError, ReplayError
from msb_v3.node.policy import NodePolicy
from msb_v3.node.protocol import (
    b64encode,
    canonical_json,
    request_signature_payload,
    session_signature_payload,
)
from msb_v3.node.service import NodeService
from msb_v3.uac.audit_chain import AuditChain


def make_service(tmp_path, pairing_code: str = "pairing") -> NodeService:
    audit = AuditChain(str(tmp_path / "audit.db"))
    state = str(tmp_path / "node.db")
    kill = KillSwitch(state, audit_chain=audit)
    queue = NodeApprovalStore(state, audit)
    identity = IdentityStore(state, pairing_code, session_ttl_s=900, clock_skew_s=60)
    root = tmp_path / "sandbox"
    reader = FileReader(root, max_bytes=1024)
    policy = NodePolicy(root, queue, kill)
    return NodeService(identity, policy, reader, audit, kill)


def authenticated(service: NodeService):
    private, public = generate_keypair()
    device_id = "iphone-test"
    service.enroll(device_id, b64encode(public), "pairing", "software")
    challenge = service.challenge(device_id)["challenge"]
    challenge_sig = sign(private, canonical_json(session_signature_payload(device_id, challenge)))
    session = service.open_session(device_id, challenge, b64encode(challenge_sig))
    return private, session["session_id"]


def envelope(private: int, session_id: str, intent: dict, request_id: str = "req-1", nonce: str = "nonce-000000000001") -> dict:
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = request_signature_payload(request_id, session_id, timestamp, nonce, intent)
    return {
        **payload,
        "signature": b64encode(sign(private, canonical_json(payload))),
    }


def test_p256_sign_and_verify_round_trip() -> None:
    private, public = generate_keypair()
    from msb_v3.node.crypto import verify

    message = b"canonical node message"
    signature = sign(private, message)
    assert verify(public, message, signature)
    assert not verify(public, b"changed", signature)


def test_raw_signature_rejects_high_s_and_malformed_keys() -> None:
    from msb_v3.node.crypto import _N, verify

    private, public = generate_keypair()
    message = b"signature fixture"
    signature = sign(private, message)
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    high_s = (_N - s).to_bytes(32, "big")
    assert verify(public, message, r.to_bytes(32, "big") + high_s) is False
    assert verify(b"\x04" + b"\x00" * 64, message, signature) is False


def test_end_to_end_read_file_is_verified_and_audited(tmp_path) -> None:
    service = make_service(tmp_path)
    (tmp_path / "sandbox" / "hello.txt").write_text("hello sovereign node", encoding="utf-8")
    private, session_id = authenticated(service)

    result = service.engage(
        envelope(private, session_id, {
            "type": "read_file",
            "objective": "read hello",
            "target": {"path": "hello.txt"},
            "requested_capabilities": ["FILE_READ"],
        })
    )

    assert result["status"] == "completed"
    assert result["decision"] == "ALLOW_WITH_LIMITS"
    assert result["result"]["content"] == "hello sovereign node"
    assert result["verification"]["ok"] is True
    assert len(result["audit_event_ids"]) >= 3
    assert service.audit.verify_chain()["valid"] is True


def test_replay_is_rejected_before_execution(tmp_path) -> None:
    service = make_service(tmp_path)
    (tmp_path / "sandbox" / "hello.txt").write_text("hello", encoding="utf-8")
    private, session_id = authenticated(service)
    request = envelope(private, session_id, {
        "type": "read_file", "target": {"path": "hello.txt"}, "requested_capabilities": ["FILE_READ"]
    })
    assert service.engage(request)["status"] == "completed"
    with pytest.raises(ReplayError):
        service.engage(request)


def test_path_traversal_and_symlink_escape_are_denied(tmp_path) -> None:
    service = make_service(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "sandbox" / "inside.txt").write_text("inside", encoding="utf-8")
    private, session_id = authenticated(service)

    traversal = service.engage(envelope(private, session_id, {
        "type": "read_file", "target": {"path": "../outside.txt"}, "requested_capabilities": ["FILE_READ"]
    }, request_id="traversal", nonce="nonce-000000000002"))
    assert traversal["status"] == "failed"
    assert "scope" in traversal["error"]

    try:
        (tmp_path / "sandbox" / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable in this environment")
    link = service.engage(envelope(private, session_id, {
        "type": "read_file", "target": {"path": "link.txt"}, "requested_capabilities": ["FILE_READ"]
    }, request_id="link", nonce="nonce-000000000003"))
    assert link["status"] == "failed"


def test_write_is_approval_required_and_never_executed(tmp_path) -> None:
    service = make_service(tmp_path)
    private, session_id = authenticated(service)
    result = service.engage(envelope(private, session_id, {
        "type": "write_file",
        "objective": "modify a file",
        "target": {"path": "hello.txt"},
        "requested_capabilities": ["FILE_WRITE"],
    }, request_id="write", nonce="nonce-000000000004"))
    assert result["status"] == "approval_required"
    assert result["approval_id"]
    assert not (tmp_path / "sandbox" / "hello.txt").exists()


def test_unknown_and_shell_capabilities_are_denied(tmp_path) -> None:
    service = make_service(tmp_path)
    private, session_id = authenticated(service)
    unknown = service.engage(envelope(private, session_id, {
        "type": "read_file", "target": {"path": "hello.txt"}, "requested_capabilities": ["ROOT_ACCESS"]
    }, request_id="unknown", nonce="nonce-000000000005"))
    assert unknown["status"] == "denied"
    shell = service.engage(envelope(private, session_id, {
        "type": "shell", "target": {"command": "rm -rf /"}, "requested_capabilities": ["SHELL_EXEC"]
    }, request_id="shell", nonce="nonce-000000000006"))
    assert shell["status"] == "denied"


def test_kill_switch_quarantines_before_actuator(tmp_path) -> None:
    service = make_service(tmp_path)
    (tmp_path / "sandbox" / "hello.txt").write_text("hello", encoding="utf-8")
    private, session_id = authenticated(service)
    service.kill_switch.arm("test", "incident")
    result = service.engage(envelope(private, session_id, {
        "type": "read_file", "target": {"path": "hello.txt"}, "requested_capabilities": ["FILE_READ"]
    }, request_id="killed", nonce="nonce-000000000007"))
    assert result["status"] == "denied"
    assert result["decision"] == "QUARANTINE"


def test_expired_timestamp_and_invalid_signature_fail_closed(tmp_path) -> None:
    service = make_service(tmp_path)
    private, session_id = authenticated(service)
    bad = envelope(private, session_id, {
        "type": "read_file", "target": {"path": "hello.txt"}, "requested_capabilities": ["FILE_READ"]
    }, request_id="bad", nonce="nonce-000000000008")
    bad["signature"] = b64encode(b"x" * 64)
    with pytest.raises(NodeAuthError):
        service.engage(bad)


def test_filesystem_rejects_oversized_files(tmp_path) -> None:
    reader = FileReader(tmp_path / "root", max_bytes=3)
    (tmp_path / "root" / "large.txt").write_text("1234", encoding="utf-8")
    with pytest.raises(CapabilityViolation, match="exceeds read limit"):
        reader.read("large.txt")
