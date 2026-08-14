from __future__ import annotations

from typing import Any

import httpx

from msb_v3.node.crypto import verify
from msb_v3.node.protocol import b64decode, canonical_json, request_signature_payload
from msb_v3.vesta.dev_harness import LoopbackDevice


def test_loopback_probe_uses_real_enrollment_session_and_signed_request() -> None:
    state: dict[str, Any] = {"challenge": "challenge-fixture", "session_id": "session-fixture"}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read()
        if request.url.path == "/node/v1/auth/enroll":
            body = httpx.Response(200, content=payload).json()
            state["device_id"] = body["device_id"]
            state["public_key"] = b64decode(body["public_key"])
            assert body["hardware_assurance"] == "software-loopback"
            return httpx.Response(
                200,
                json={
                    "device_id": body["device_id"],
                    "status": "ACTIVE",
                    "hardware_assurance": body["hardware_assurance"],
                },
                request=request,
            )
        if request.url.path == "/node/v1/auth/challenge":
            return httpx.Response(
                200,
                json={"device_id": state["device_id"], "challenge": state["challenge"]},
                request=request,
            )
        if request.url.path == "/node/v1/auth/session":
            body = httpx.Response(200, content=payload).json()
            signed = canonical_json(
                {"protocol": "node.v1", "device_id": body["device_id"], "challenge": body["challenge"]}
            )
            assert verify(state["public_key"], signed, b64decode(body["signature"]))
            return httpx.Response(
                200,
                json={
                    "session_id": state["session_id"],
                    "device_id": state["device_id"],
                    "expires_at": "2099-01-01T00:00:00+00:00",
                },
                request=request,
            )
        if request.url.path == "/vesta/signed-chat":
            body = httpx.Response(200, content=payload).json()
            unsigned = {key: value for key, value in body.items() if key != "signature"}
            expected = request_signature_payload(
                unsigned["request_id"],
                unsigned["session_id"],
                unsigned["timestamp"],
                unsigned["nonce"],
                unsigned["intent"],
            )
            assert unsigned == expected
            assert verify(state["public_key"], canonical_json(expected), b64decode(body["signature"]))
            assert "filesystem.write" in body["intent"]["requested_capabilities"]
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "bind_id": "bind_fixture",
                    "task_id": "task_fixture",
                    "evidence_refs": ["ev_fixture"],
                    "decision": "ALLOW",
                    "policy_version": "vesta-policy-1",
                    "payload": {"query": body["intent"]["target"]["query"], "text": "LOOPBACK_OK", "model": "fixture"},
                    "error": None,
                    "audit_event_ids": [1, 2],
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    device = LoopbackDevice(device_id="loopback-test")
    device._client.close()
    device._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:8766",
    )
    try:
        result = device.probe("pairing", "Reply with exactly LOOPBACK_OK.")
    finally:
        device.close()

    assert result["device_id"] == "loopback-test"
    assert result["hardware_assurance"] == "software-loopback"
    assert result["chat"]["payload"]["text"] == "LOOPBACK_OK"
