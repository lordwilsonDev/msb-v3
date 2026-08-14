"""Versioned canonical serialization for Sovereign Node messages."""

from __future__ import annotations

import base64
import json
from typing import Any, Dict

PROTOCOL_VERSION = "node.v1"


def b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def canonical_json(value: Dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def session_signature_payload(device_id: str, challenge: str) -> Dict[str, Any]:
    return {"protocol": PROTOCOL_VERSION, "device_id": device_id, "challenge": challenge}


def request_signature_payload(
    request_id: str,
    session_id: str,
    timestamp: str,
    nonce: str,
    intent: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "protocol": PROTOCOL_VERSION,
        "request_id": request_id,
        "session_id": session_id,
        "timestamp": timestamp,
        "nonce": nonce,
        "intent": intent,
    }
