"""Hardware-independent signed-device harness for local Vesta development.

This client exercises the real enrollment, challenge, session, replay, and
signed-chat endpoints over loopback. It deliberately generates an ephemeral
P-256 identity per instance and never changes production policy or admission.
Use the real iPhone client and private tunnel for any remote deployment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

import httpx

from msb_v3.node.crypto import generate_keypair, sign
from msb_v3.node.protocol import (
    b64encode,
    canonical_json,
    request_signature_payload,
    session_signature_payload,
)


@dataclass
class LoopbackDevice:
    """Ephemeral device identity that speaks the production node.v1 protocol."""

    base_url: str = "http://127.0.0.1:8766"
    device_id: str = field(default_factory=lambda: f"loopback-{uuid4().hex}")
    timeout_s: float = 30.0
    _private_key: int = field(init=False, repr=False)
    _public_key: bytes = field(init=False, repr=False)
    _session_id: str | None = field(default=None, init=False, repr=False)
    _client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._private_key, self._public_key = generate_keypair()
        self._client = httpx.Client(base_url=self.base_url.rstrip("/"), timeout=self.timeout_s)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LoopbackDevice":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def public_key(self) -> str:
        return b64encode(self._public_key)

    def enroll(self, pairing_code: str) -> Dict[str, Any]:
        response = self._client.post(
            "/node/v1/auth/enroll",
            json={
                "device_id": self.device_id,
                "public_key": self.public_key,
                "pairing_code": pairing_code,
                "hardware_assurance": "software-loopback",
            },
        )
        response.raise_for_status()
        return response.json()

    def authenticate(self) -> Dict[str, Any]:
        challenge_response = self._client.post(
            "/node/v1/auth/challenge",
            json={"device_id": self.device_id},
        )
        challenge_response.raise_for_status()
        challenge = challenge_response.json()["challenge"]
        signature = b64encode(
            sign(
                self._private_key,
                canonical_json(session_signature_payload(self.device_id, challenge)),
            )
        )
        session_response = self._client.post(
            "/node/v1/auth/session",
            json={
                "device_id": self.device_id,
                "challenge": challenge,
                "signature": signature,
            },
        )
        session_response.raise_for_status()
        result = session_response.json()
        self._session_id = result["session_id"]
        return result

    def signed_chat_payload(self, query: str, *, request_id: str | None = None) -> Dict[str, Any]:
        if not self._session_id:
            raise RuntimeError("authenticate the loopback device before signing a request")
        intent = {
            "type": "chat",
            "objective": query,
            "target": {"query": query},
            # Deliberately include an escalation attempt in the fixture. Vesta
            # must ignore this field and apply its server-owned capabilities.
            "requested_capabilities": ["model.inference", "memory.read", "filesystem.write"],
        }
        payload = request_signature_payload(
            request_id or f"loopback-request-{uuid4().hex}",
            self._session_id,
            datetime.now(timezone.utc).isoformat(),
            f"loopback-nonce-{uuid4().hex}",
            intent,
        )
        return {
            **payload,
            "signature": b64encode(sign(self._private_key, canonical_json(payload))),
        }

    def signed_chat(self, query: str) -> Dict[str, Any]:
        response = self._client.post("/vesta/signed-chat", json=self.signed_chat_payload(query))
        response.raise_for_status()
        return response.json()

    def signed_read_file(self, path: str) -> Dict[str, Any]:
        if not self._session_id:
            raise RuntimeError("authenticate the loopback device before signing a request")
        intent = {
            "type": "read_file",
            "objective": f"Read {path}",
            "target": {"path": path},
            "requested_capabilities": ["FILE_READ"],
        }
        payload = request_signature_payload(
            f"loopback-read-{uuid4().hex}",
            self._session_id,
            datetime.now(timezone.utc).isoformat(),
            f"loopback-read-nonce-{uuid4().hex}",
            intent,
        )
        body = {
            **payload,
            "signature": b64encode(sign(self._private_key, canonical_json(payload))),
        }
        response = self._client.post("/node/v1/engage", json=body)
        response.raise_for_status()
        return response.json()

    def probe(self, pairing_code: str, query: str = "Reply with exactly LOOPBACK_OK.") -> Dict[str, Any]:
        """Run the complete local enrollment → session → signed chat probe."""
        enrollment = self.enroll(pairing_code)
        session = self.authenticate()
        chat = self.signed_chat(query)
        return {
            "device_id": self.device_id,
            "hardware_assurance": enrollment.get("hardware_assurance"),
            "session_id": session.get("session_id"),
            "chat": chat,
        }
