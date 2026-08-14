from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import msb_v3.node.api as node_api
from msb_v3.api.app import create_app
from msb_v3.core.config import settings
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.node.approval import NodeApprovalStore
from msb_v3.node.crypto import generate_keypair, sign
from msb_v3.node.filesystem import FileReader
from msb_v3.node.identity import IdentityStore
from msb_v3.node.policy import NodePolicy
from msb_v3.node.protocol import (
    b64encode,
    canonical_json,
    request_signature_payload,
    session_signature_payload,
)
from msb_v3.node.service import NodeService
from msb_v3.uac.audit_chain import AuditChain


def test_gateway_signed_read_flow(tmp_path) -> None:
    audit = AuditChain(str(tmp_path / "audit.db"))
    state = str(tmp_path / "node.db")
    kill = KillSwitch(state, audit_chain=audit)
    approvals = NodeApprovalStore(state, audit)
    identity = IdentityStore(state, "pairing")
    reader = FileReader(tmp_path / "sandbox")
    (tmp_path / "sandbox" / "gateway.txt").write_text("gateway works", encoding="utf-8")
    node_api._service = NodeService(identity, NodePolicy(reader.root, approvals, kill), reader, audit, kill)

    private, public = generate_keypair()
    client = TestClient(create_app())
    enroll = client.post("/node/v1/auth/enroll", json={
        "device_id": "iphone-api",
        "public_key": b64encode(public),
        "pairing_code": "pairing",
        "hardware_assurance": "software",
    })
    assert enroll.status_code == 200
    challenge = client.post("/node/v1/auth/challenge", json={"device_id": "iphone-api"}).json()["challenge"]
    challenge_sig = b64encode(sign(private, canonical_json(session_signature_payload("iphone-api", challenge))))
    session = client.post("/node/v1/auth/session", json={
        "device_id": "iphone-api", "challenge": challenge, "signature": challenge_sig,
    })
    assert session.status_code == 200
    session_id = session.json()["session_id"]

    request_id = "gateway-request"
    nonce = "gateway-nonce-000001"
    timestamp = datetime.now(timezone.utc).isoformat()
    intent = {
        "type": "read_file",
        "objective": "read gateway fixture",
        "target": {"path": "gateway.txt"},
        "requested_capabilities": ["FILE_READ"],
    }
    payload = request_signature_payload(request_id, session_id, timestamp, nonce, intent)
    signed = {**payload, "signature": b64encode(sign(private, canonical_json(payload)))}
    response = client.post("/node/v1/engage", json=signed)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"]["content"] == "gateway works"


def test_node_surface_requires_tunnel_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app())
    # Tunnel off (default): the executor surface is reachable.
    assert client.get("/node/v1/status").status_code == 200
    monkeypatch.setattr(settings, "vesta_require_tunnel", True)
    monkeypatch.setattr(settings, "vesta_allowed_cidrs", "127.0.0.1/32,::1/128")
    # Tunnel required: the raw /node/v1 surface is not public.
    assert client.get("/node/v1/status").status_code == 403


def test_node_auth_endpoints_are_rate_limited() -> None:
    """The enroll/challenge/session handshake is per-client capped so an
    unauthenticated caller who knows a device_id cannot spam challenge
    issuance. Exhaust the window, then verify the 429 fail-closed."""
    client = TestClient(create_app())
    node_api._AUTH_LIMITER.clear()

    # 10 allowed, 11th must be refused.
    for _ in range(10):
        response = client.post("/node/v1/auth/challenge", json={"device_id": "spam-device"})
        assert response.status_code == 401  # not a real device, but counted

    response = client.post("/node/v1/auth/challenge", json={"device_id": "spam-device"})
    assert response.status_code == 429

    # Clean the shared limiter so other tests are unaffected.
    node_api._AUTH_LIMITER.clear()
