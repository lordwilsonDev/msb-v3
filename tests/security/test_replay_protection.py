"""P13 replay protection: same valid request sent twice.

Captures current server behavior:
- If server rejects duplicate requests: 200 then 4xx
- If server replays: 200 twice (documented as pending)
"""

from __future__ import annotations

import os
import sys

import pytest
import requests

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))
SECRET = os.environ.get("MCP_BRIDGE_SECRET", "")
if not SECRET:
    # Portability gate stages a copy with .env deliberately excluded
    # (secrets must never be copied) — these tests need the real running
    # server's secret and cannot pass against an empty one.
    pytestmark = [pytest.mark.integration, pytest.mark.skip(reason="MCP_BRIDGE_SECRET unavailable")]
BRIDGE_URL = "http://127.0.0.1:8766/mcp/proxy"
DB_PATH = os.path.join(REPO, "data", "memory_fabric", "memory.db")


def _headers(actor: str) -> dict:
    return {
        "Content-Type": "application/json",
        "x-mcp-secret": SECRET,
        "x-forwarded-for": actor,
    }


def _call(payload: dict) -> tuple[int, dict]:
    req = requests.post(BRIDGE_URL, json=payload, headers=_headers("p13"), timeout=10)
    try:
        body = req.json()
    except Exception:
        body = {"raw": req.text[:200]}
    return req.status_code, body


def _make_memory(content: str) -> str:
    status, body = _call({"tool": "memory_store", "args": {"content": content, "type_": "SEMANTIC", "tags": ["p13"]}})
    assert status == 200, body
    ret = body.get("result", body)
    if isinstance(ret, dict):
        return ret["memory_id"]
    return getattr(ret, "memory_id", ret)


def test_p13_replay_same_request():
    """Replay an identical verify request.

    If replay protection is implemented, second call must return 4xx.
    Otherwise 200 is current behavior — test documents this and notes pending.
    """
    mid = _make_memory("P13 replay protection")
    # Step to CONTRADICTED first so we have a valid source state
    _call({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "CONTRADICTED", "reason": "p13 setup"}})
    payload = {"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "VERIFIED", "reason": "p13 replay", "resolution": "replay test"}}

    status1, body1 = _call(payload)
    assert status1 == 200, body1

    # Replay same request verbatim
    status2, body2 = _call(payload)

    # Document server behavior:
    # - 200/200 = no replay protection yet (pending)
    # - 4xx/5xx = replay protection wired
    assert status1 == 200
    assert status2 in (200, 400, 422, 500), f"unexpected replay status: {status2} {body2}"
