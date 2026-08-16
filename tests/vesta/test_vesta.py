from __future__ import annotations

import ipaddress
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app
from msb_v3.core.config import settings
from msb_v3.core.container import build_container
from msb_v3.evidence.spine import DecisionEvidenceStore
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.harnesses.base import HarnessResult
from msb_v3.node.crypto import generate_keypair, sign
from msb_v3.node.filesystem import FileReader, FileWriter
from msb_v3.node.identity import IdentityStore
from msb_v3.node.protocol import (
    b64encode,
    canonical_json,
    request_signature_payload,
    session_signature_payload,
)
from msb_v3.uac.audit_chain import AuditChain
from msb_v3.vesta.adapter import VestaMSBAdapter
from msb_v3.vesta.approvals import VestaApprovalStore
from msb_v3.vesta.evidence import EvidenceStore
from msb_v3.vesta.models import ABind
from msb_v3.vesta.policy import authorize_chat
from msb_v3.vesta.read import VestaReadService
from msb_v3.vesta.runtime import VestaTaskStore
from msb_v3.vesta.services import VestaServices
from msb_v3.vesta.shell import ShellExecutor, VestaShellApprovalStore, VestaShellService
from msb_v3.vesta.transport import TransportAdmission
from msb_v3.vesta.write import VestaWriteService


class FakeChat:
    def __init__(self) -> None:
        self.calls: list[Dict[str, Any]] = []

    def execute(self, query: str, context: Dict[str, Any] | None = None, *, session: str = "default", **kwargs: Any) -> HarnessResult:
        self.calls.append({"query": query, "context": context or {}, "session": session})
        return HarnessResult(
            ok=True,
            event="chat:completed",
            payload={"query": query, "text": "fake response", "model": "test-model"},
        )


def signed_device_session(identity: IdentityStore, device_id: str, private: Any, public: bytes) -> str:
    identity.enroll(device_id, b64encode(public), "pairing", "software")
    challenge = identity.challenge(device_id)
    signature = b64encode(sign(private, canonical_json(session_signature_payload(device_id, challenge))))
    return identity.open_session(device_id, challenge, signature)["session_id"]


@pytest.fixture
def services(tmp_path: Path) -> VestaServices:
    audit = AuditChain(str(tmp_path / "audit.db"))
    tasks = VestaTaskStore(str(tmp_path / "tasks.db"))
    evidence = EvidenceStore(str(tmp_path / "evidence"), str(tmp_path / "evidence.db"))
    approvals = VestaApprovalStore(str(tmp_path / "tasks.db"))
    shell_approvals = VestaShellApprovalStore(str(tmp_path / "tasks.db"))
    spine = DecisionEvidenceStore(str(tmp_path / "spine.db"))
    return VestaServices(
        audit=audit,
        tasks=tasks,
        evidence=evidence,
        spine=spine,
        adapter=VestaMSBAdapter(audit, tasks, evidence, spine=spine),
        write_approvals=approvals,
        shell_approvals=shell_approvals,
        signed_identity=IdentityStore(str(tmp_path / "signed.db"), "pairing"),
        read_service=VestaReadService(
            audit,
            tasks,
            evidence,
            FileReader(tmp_path / "sandbox", max_bytes=100),
            KillSwitch(str(tmp_path / "read-kill.db"), audit_chain=audit),
        ),
        shell_service=VestaShellService(
            audit,
            tasks,
            evidence,
            shell_approvals,
            ShellExecutor(tmp_path / "sandbox", timeout_s=1.0, max_output_bytes=128),
            KillSwitch(str(tmp_path / "shell-kill.db"), audit_chain=audit),
        ),
        write_service=VestaWriteService(
            audit,
            tasks,
            evidence,
            approvals,
            FileWriter(tmp_path / "sandbox", max_bytes=100),
            KillSwitch(str(tmp_path / "kill.db"), audit_chain=audit),
        ),
    )


@pytest.fixture
def vesta_client(services: VestaServices, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, FakeChat, AuditChain]:
    monkeypatch.setattr(settings, "operator_token", "operator-secret")
    app = create_app()
    app.state.container = build_container(vesta=services)
    fake = FakeChat()
    app.state.chat = fake
    return TestClient(app), fake, services.audit


def test_policy_allows_only_phase_zero_to_two_capabilities() -> None:
    allowed = ABind.create("session-1", ["model.inference", "memory.read"])
    assert authorize_chat(allowed).decision == "ALLOW"

    mutation = ABind.create("session-1", ["model.inference", "filesystem.write"])
    denied = authorize_chat(mutation)
    assert denied.decision == "DENY"
    assert "filesystem.write" in denied.reasons[0]

    unknown = ABind.create("session-1", ["model.inference", "future.magic"])
    assert authorize_chat(unknown).decision == "DENY"
    assert "unknown capabilities" in authorize_chat(unknown).reasons[0]


def test_vesta_chat_creates_bind_records_events_and_propagates_context(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
) -> None:
    client, fake, audit = vesta_client
    response = client.post(
        "/vesta/chat",
        headers={"Authorization": "Bearer operator-secret"},
        json={"query": "hello", "session": "s1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["decision"] == "ALLOW"
    assert body["bind_id"].startswith("bind_")
    assert body["task_id"].startswith("task_")
    assert len(body["audit_event_ids"]) == 10
    assert len(body["evidence_refs"]) == 3
    assert fake.calls[0]["context"]["vesta_bind"]["bind_id"] == body["bind_id"]

    records = audit.get_chain(component="vesta")
    assert records[0].event_type == "request.received"
    assert records[-1].event_type == "response.returned"
    assert [record.payload.get("to_state") for record in records if record.event_type == "task.transition"] == [
        "AUTHENTICATED",
        "PLANNED",
        "AUTHORIZED",
        "EXECUTING",
        "VERIFYING",
        "COMPLETED",
    ]
    assert all(record.payload.get("bind_id", body["bind_id"]) == body["bind_id"] for record in records)
    task = client.get(
        f"/vesta/tasks/{body['task_id']}",
        headers={"Authorization": "Bearer operator-secret"},
    )
    assert task.status_code == 200
    assert task.json()["state"] == "COMPLETED"
    assert task.json()["metadata"]["evidence_refs"] == body["evidence_refs"]
    assert len(task.json()["transitions"]) == 7
    for evidence_id in body["evidence_refs"]:
        evidence = client.get(
            f"/vesta/evidence/{evidence_id}",
            headers={"Authorization": "Bearer operator-secret"},
        )
        assert evidence.status_code == 200
        assert evidence.json()["verified"] is True
    assert audit.verify_chain()["valid"] is True


def test_vesta_chat_emits_decision_spine_record(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
    services: VestaServices,
) -> None:
    client, _, _ = vesta_client
    response = client.post(
        "/vesta/chat",
        headers={"Authorization": "Bearer operator-secret"},
        json={"query": "hello spine", "session": "s1"},
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]

    # Phase 2.2: one governed chat produces the full causal chain — decision
    # → execution → result → verification — each vertebra linking back to the
    # decision and cross-linked to its audit event.
    trail = services.spine.trail(task_id)
    assert [r.evidence.kind for r in trail] == [
        "decision",
        "execution",
        "result",
        "verification",
    ]
    decision, execution, result, verification = trail
    assert decision.evidence.policy_result == "ALLOW"
    assert decision.evidence.capability_requested == ("memory.read", "model.inference")
    assert decision.evidence.capability_granted == ("memory.read", "model.inference")
    assert decision.evidence.risk_level == "normal"
    assert decision.audit_seq is not None
    # every vertebra links back to the authorizing decision
    assert all(r.evidence.parent_decision_id == decision.decision_id for r in trail[1:])
    # the result vertebra carries the model + the response evidence id
    assert result.evidence.model_id == "test-model"
    assert result.evidence.result_id is not None
    assert result.evidence.result_id in response.json()["evidence_refs"]
    # the verification vertebra carries the response digest
    assert verification.evidence.verification_id is not None
    # cross-link: the decision points at the authorization.decided audit event
    decided = next(
        r
        for r in services.audit.get_chain(component="vesta")
        if r.event_type == "authorization.decided" and r.payload.get("task_id") == task_id
    )
    assert decision.audit_seq == decided.seq
    assert services.spine.verify_chain()["valid"] is True


def test_vesta_denied_chat_emits_only_decision_spine(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
    services: VestaServices,
) -> None:
    """A denied action never executes: the spine holds the single decision
    record, with no execution/result/verification vertebrae."""
    client, _, _ = vesta_client
    response = client.post(
        "/vesta/chat",
        headers={"Authorization": "Bearer operator-secret"},
        json={"query": "delete files", "capabilities": ["filesystem.write"]},
    )
    assert response.status_code == 403
    task_id = response.json()["detail"]["task_id"]
    trail = services.spine.trail(task_id)
    assert [r.evidence.kind for r in trail] == ["decision"]
    assert trail[0].evidence.policy_result == "DENY"
    assert trail[0].evidence.capability_granted == ()


def test_signed_chat_admits_enrolled_device_and_rejects_replay(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
    services: VestaServices,
) -> None:
    client, fake, _ = vesta_client
    identity = services.signed_identity
    private, public = generate_keypair()
    device_id = "iphone-signed"
    identity.enroll(device_id, b64encode(public), "pairing", "software")
    challenge = identity.challenge(device_id)
    challenge_signature = b64encode(
        sign(private, canonical_json(session_signature_payload(device_id, challenge)))
    )
    session_id = identity.open_session(device_id, challenge, challenge_signature)["session_id"]
    intent = {
        "type": "chat",
        "objective": "signed status request",
        "target": {"query": "Reply with exactly SIGNED_CHAT_OK and nothing else."},
        "requested_capabilities": ["filesystem.write"],
    }
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = request_signature_payload("signed-1", session_id, timestamp, "signed-nonce-000001", intent)
    body = {**payload, "signature": b64encode(sign(private, canonical_json(payload)))}

    response = client.post("/vesta/signed-chat", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["payload"]["query"] == "Reply with exactly SIGNED_CHAT_OK and nothing else."
    assert result["payload"]["text"] == "fake response"
    assert fake.calls[0]["context"]["vesta_bind"]["actor"] == device_id
    assert result["decision"] == "ALLOW"

    replay = client.post("/vesta/signed-chat", json=body)
    assert replay.status_code == 409

    (services.read_service.reader.root / "signed-read.txt").write_text("signed read")
    read_intent = {
        "type": "read_file",
        "objective": "read a sandbox file",
        "target": {"path": "signed-read.txt"},
        "requested_capabilities": ["filesystem.write"],
    }
    read_timestamp = datetime.now(timezone.utc).isoformat()
    read_payload = request_signature_payload(
        "signed-read-1", session_id, read_timestamp, "signed-read-nonce-000001", read_intent
    )
    read_body = {**read_payload, "signature": b64encode(sign(private, canonical_json(read_payload)))}
    signed_read = client.post("/vesta/signed-read", json=read_body)
    assert signed_read.status_code == 200
    signed_read_result = signed_read.json()
    assert signed_read_result["status"] == "completed"
    assert signed_read_result["result"]["content"] == "signed read"
    assert services.tasks.get(signed_read_result["task_id"])["actor"] == device_id


def test_signed_owner_ack_requires_exact_contract_and_cannot_replay(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
    services: VestaServices,
) -> None:
    client, _, _ = vesta_client
    headers = {"Authorization": "Bearer operator-secret"}
    pending = client.post(
        "/vesta/shell/execute",
        headers=headers,
        json={"executable": "echo", "args": ["SIGNED_ACK_OK"]},
    )
    assert pending.status_code == 200
    approval = pending.json()
    assert approval["command_sha256"]

    private, public = generate_keypair()
    session_id = signed_device_session(services.signed_identity, "iphone-owner", private, public)

    def signed_ack(request_id: str, nonce: str, command_sha256: str) -> dict[str, Any]:
        intent = {
            "type": "shell_approval",
            "objective": "Approve the exact shell contract",
            "target": {
                "approval_id": approval["approval_id"],
                "command_sha256": command_sha256,
                "policy_version": approval["policy_version"],
            },
            "requested_capabilities": ["human.request_ack"],
        }
        timestamp = datetime.now(timezone.utc).isoformat()
        payload = request_signature_payload(request_id, session_id, timestamp, nonce, intent)
        return {**payload, "signature": b64encode(sign(private, canonical_json(payload)))}

    wrong = client.post(
        f"/vesta/shell/approvals/{approval['approval_id']}/signed-approve",
        json=signed_ack("signed-ack-wrong", "signed-ack-nonce-wrong", "0" * 64),
    )
    assert wrong.status_code == 409
    assert services.shell_approvals.get(approval["approval_id"])["status"] == "PENDING"

    accepted = client.post(
        f"/vesta/shell/approvals/{approval['approval_id']}/signed-approve",
        json=signed_ack("signed-ack-ok", "signed-ack-nonce-ok", approval["command_sha256"]),
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "completed"
    stored = services.shell_approvals.get(approval["approval_id"])
    assert stored["status"] == "APPROVED"
    assert stored["decided_by"] == "iphone-owner"

    replay = client.post(
        f"/vesta/shell/approvals/{approval['approval_id']}/signed-approve",
        json=signed_ack("signed-ack-ok", "signed-ack-nonce-ok", approval["command_sha256"]),
    )
    assert replay.status_code == 409


def test_signed_owner_ack_expiry_fails_closed(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
    services: VestaServices,
) -> None:
    client, _, _ = vesta_client
    headers = {"Authorization": "Bearer operator-secret"}
    pending = client.post(
        "/vesta/shell/execute",
        headers=headers,
        json={"executable": "echo", "args": ["EXPIRED_ACK"]},
    ).json()
    with sqlite3.connect(services.shell_approvals.db_path) as conn:
        conn.execute(
            "UPDATE vesta_shell_approvals SET expires_at=? WHERE approval_id=?",
            ("2000-01-01T00:00:00+00:00", pending["approval_id"]),
        )
    private, public = generate_keypair()
    session_id = signed_device_session(services.signed_identity, "iphone-expired", private, public)
    intent = {
        "type": "shell_approval",
        "objective": "Approve the exact shell contract",
        "target": {
            "approval_id": pending["approval_id"],
            "command_sha256": pending["command_sha256"],
            "policy_version": pending["policy_version"],
        },
        "requested_capabilities": ["human.request_ack"],
    }
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = request_signature_payload("signed-ack-expired", session_id, timestamp, "signed-ack-expired-nonce", intent)
    response = client.post(
        f"/vesta/shell/approvals/{pending['approval_id']}/signed-approve",
        json={**payload, "signature": b64encode(sign(private, canonical_json(payload)))},
    )
    assert response.status_code == 409
    assert services.shell_approvals.get(pending["approval_id"])["status"] == "EXPIRED", response.json()


def test_signed_file_write_ack_requires_exact_contract_and_cannot_replay(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
    services: VestaServices,
    tmp_path: Path,
) -> None:
    client, _, _ = vesta_client
    headers = {"Authorization": "Bearer operator-secret"}
    pending = client.post(
        "/vesta/execute",
        headers=headers,
        json={"path": "signed-write.txt", "content": "SIGNED_WRITE_OK"},
    )
    assert pending.status_code == 200
    approval = pending.json()
    assert approval["target_path"] == "signed-write.txt"
    assert approval["payload_sha256"]
    assert approval["expected_sha256"] is None

    private, public = generate_keypair()
    session_id = signed_device_session(services.signed_identity, "iphone-write-owner", private, public)

    def signed_ack(request_id: str, nonce: str, target_path: str, payload_sha256: str) -> dict[str, Any]:
        intent = {
            "type": "file_write_approval",
            "objective": "Approve the exact file-write contract",
            "target": {
                "approval_id": approval["approval_id"],
                "target_path": target_path,
                "payload_sha256": payload_sha256,
                "expected_sha256": "",
                "policy_version": approval["policy_version"],
            },
            "requested_capabilities": ["human.request_ack"],
        }
        timestamp = datetime.now(timezone.utc).isoformat()
        payload = request_signature_payload(request_id, session_id, timestamp, nonce, intent)
        return {**payload, "signature": b64encode(sign(private, canonical_json(payload)))}

    wrong = client.post(
        f"/vesta/approvals/{approval['approval_id']}/signed-approve",
        json=signed_ack("write-ack-wrong", "write-ack-nonce-wrong", "other.txt", approval["payload_sha256"]),
    )
    assert wrong.status_code == 409
    assert services.write_approvals.get(approval["approval_id"])["status"] == "PENDING"

    accepted = client.post(
        f"/vesta/approvals/{approval['approval_id']}/signed-approve",
        json=signed_ack("write-ack-ok", "write-ack-nonce-ok", "signed-write.txt", approval["payload_sha256"]),
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "completed"
    assert (tmp_path / "sandbox" / "signed-write.txt").read_text() == "SIGNED_WRITE_OK"
    stored = services.write_approvals.get(approval["approval_id"])
    assert stored["status"] == "APPROVED"
    assert stored["decided_by"] == "iphone-write-owner"

    replay = client.post(
        f"/vesta/approvals/{approval['approval_id']}/signed-approve",
        json=signed_ack("write-ack-ok", "write-ack-nonce-ok", "signed-write.txt", approval["payload_sha256"]),
    )
    assert replay.status_code == 409


def test_signed_file_write_ack_expiry_is_persisted_and_fail_closed(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
    services: VestaServices,
) -> None:
    client, _, _ = vesta_client
    headers = {"Authorization": "Bearer operator-secret"}
    pending = client.post(
        "/vesta/execute",
        headers=headers,
        json={"path": "expired-write.txt", "content": "EXPIRED"},
    ).json()
    with sqlite3.connect(services.write_approvals.db_path) as conn:
        conn.execute(
            "UPDATE vesta_approvals SET expires_at=? WHERE approval_id=?",
            ("2000-01-01T00:00:00+00:00", pending["approval_id"]),
        )
    private, public = generate_keypair()
    session_id = signed_device_session(services.signed_identity, "iphone-write-expired", private, public)
    intent = {
        "type": "file_write_approval",
        "objective": "Approve the exact file-write contract",
        "target": {
            "approval_id": pending["approval_id"],
            "target_path": pending["target_path"],
            "payload_sha256": pending["payload_sha256"],
            "expected_sha256": "",
            "policy_version": pending["policy_version"],
        },
        "requested_capabilities": ["human.request_ack"],
    }
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = request_signature_payload("write-ack-expired", session_id, timestamp, "write-ack-expired-nonce", intent)
    response = client.post(
        f"/vesta/approvals/{pending['approval_id']}/signed-approve",
        json={**payload, "signature": b64encode(sign(private, canonical_json(payload)))},
    )
    assert response.status_code == 409
    assert services.write_approvals.get(pending["approval_id"])["status"] == "EXPIRED"


def test_vesta_file_read_api_returns_verified_evidence(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
    tmp_path: Path,
) -> None:
    client, _, _ = vesta_client
    (tmp_path / "sandbox" / "read-api.txt").write_text("read through Vesta")
    response = client.post(
        "/vesta/read",
        headers={"Authorization": "Bearer operator-secret"},
        json={"path": "read-api.txt"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"]["content"] == "read through Vesta"
    assert body["verification"]["ok"] is True
    assert len(body["evidence_refs"]) == 4
    assert client.get(
        f"/vesta/tasks/{body['task_id']}",
        headers={"Authorization": "Bearer operator-secret"},
    ).json()["state"] == "COMPLETED"


def test_vesta_shell_api_requires_approval_and_returns_verified_receipt(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
) -> None:
    client, _, _ = vesta_client
    headers = {"Authorization": "Bearer operator-secret"}
    pending = client.post(
        "/vesta/shell/execute",
        headers=headers,
        json={"executable": "echo", "args": ["API_SHELL_OK"]},
    )
    assert pending.status_code == 200
    body = pending.json()
    assert body["status"] == "approval_required"
    approved = client.post(
        f"/vesta/shell/approvals/{body['approval_id']}/approve",
        headers=headers,
    )
    assert approved.status_code == 200
    result = approved.json()
    assert result["status"] == "completed"
    assert result["verification"]["ok"] is True


def test_vesta_file_write_api_requires_and_executes_exact_approval(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
) -> None:
    client, _, _ = vesta_client
    headers = {"Authorization": "Bearer operator-secret"}
    pending = client.post(
        "/vesta/execute",
        headers=headers,
        json={"path": "api-write.txt", "content": "approved content"},
    )
    assert pending.status_code == 200
    pending_body = pending.json()
    assert pending_body["status"] == "approval_required"
    assert client.get(
        f"/vesta/approvals/{pending_body['approval_id']}",
        headers=headers,
    ).json()["status"] == "PENDING"

    approved = client.post(
        f"/vesta/approvals/{pending_body['approval_id']}/approve",
        headers=headers,
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "completed"
    task = client.get(
        f"/vesta/tasks/{pending_body['task_id']}",
        headers=headers,
    ).json()
    assert task["state"] == "COMPLETED"


def test_vesta_denies_capability_escalation_before_msb(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
) -> None:
    client, fake, audit = vesta_client
    response = client.post(
        "/vesta/chat",
        headers={"Authorization": "Bearer operator-secret"},
        json={"query": "delete files", "capabilities": ["filesystem.write"]},
    )
    assert response.status_code == 403
    assert fake.calls == []
    assert response.json()["detail"]["decision"] == "DENY"
    records = audit.get_chain(component="vesta")
    assert [record.event_type for record in records] == [
        "request.received",
        "task.transition",
        "task.transition",
        "authorization.decided",
        "task.transition",
        "request.denied",
    ]
    assert records[-2].payload["to_state"] == "DENIED"


def test_vesta_controlled_chat_is_fail_closed_without_auth(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = vesta_client
    assert client.post("/vesta/chat", json={"query": "hello"}).status_code == 401
    monkeypatch.setattr(settings, "operator_token", "")
    assert client.post(
        "/vesta/chat",
        headers={"Authorization": "Bearer operator-secret"},
        json={"query": "hello"},
    ).status_code == 503


def test_vesta_observation_and_ledger_views_are_read_only(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
) -> None:
    client, _, _ = vesta_client
    assert client.get("/vesta/status").json()["mode"] == "phase-0-2"
    assert client.get("/vesta/manifest").json()["integrity_status"] == "not_attested"
    catalog = client.get("/vesta/capabilities").json()["capabilities"]
    assert any(item["capability"] == "model.inference" and item["enabled"] for item in catalog)
    discovered = {item["path"] for item in client.get("/vesta/routes").json()["routes"]}
    assert {"/vesta/status", "/vesta/chat", "/vesta/ledger/verify"}.issubset(discovered)
    assert client.get("/vesta/ledger/verify").json()["valid"] is True


def test_tunnel_admission_uses_direct_peer_address() -> None:
    admission = TransportAdmission(
        required=True,
        allowed_networks=(ipaddress.ip_network("10.77.0.0/24"),),
    )
    assert admission.allows("10.77.0.12") is True
    assert admission.allows("192.168.1.10") is False
    assert admission.allows("spoofed-forwarded-host") is False


def test_controlled_route_rejects_non_tunnel_peer_when_required(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, fake, _ = vesta_client
    monkeypatch.setattr(settings, "vesta_require_tunnel", True)
    response = client.post(
        "/vesta/chat",
        headers={"Authorization": "Bearer operator-secret"},
        json={"query": "hello"},
    )
    assert response.status_code == 403
    assert fake.calls == []


def test_read_only_vesta_views_require_tunnel_when_enabled(
    vesta_client: tuple[TestClient, FakeChat, AuditChain],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = vesta_client
    monkeypatch.setattr(settings, "vesta_require_tunnel", True)
    monkeypatch.setattr(settings, "vesta_allowed_cidrs", "127.0.0.1/32,::1/128")
    # The TestClient peer host is not an IP, so every read-only view must fail
    # closed when tunnel admission is required — not just the mutation routes.
    for path in (
        "/vesta/status",
        "/vesta/manifest",
        "/vesta/capabilities",
        "/vesta/routes",
        "/vesta/ledger/verify",
    ):
        assert client.get(path).status_code == 403, path


def test_ledger_tampering_is_detected_without_repair(tmp_path: Path) -> None:
    path = tmp_path / "audit.db"
    audit = AuditChain(str(path))
    audit.append("vesta", "test", {"value": "original"})
    audit.append("vesta", "test", {"value": "second"})
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE audit_records SET payload=? WHERE seq=1", ('{"value":"tampered"}',))
    result = audit.verify_chain()
    assert result["valid"] is False
    assert result["broken_at_seq"] == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_records").fetchone()[0] == 2
