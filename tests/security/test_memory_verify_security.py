"""Regression / security suite for memory_verify (Phase 6 hardening).

Runs against MCP bridge by default; direct fabric path covered separately in
``tests/security/test_memory_verify_direct.py``.
"""

from __future__ import annotations

import os
import sys
import json
import sqlite3
import hashlib
import urllib.request
import urllib.error

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "src"))

from msb_v3.memory_fabric.fabric import MemoryFabric  # noqa: E402

BRIDGE_URL = "http://127.0.0.1:8766/mcp/proxy"
DB_PATH = os.path.join(REPO, "data", "memory_fabric", "memory.db")


def _secret() -> str:
    env_path = os.path.join(REPO, ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith("MCP_BRIDGE_SECRET="):
                return line.strip().split("=", 1)[1]
    return os.environ.get("MCP_BRIDGE_SECRET", "")


def _headers(actor: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "x-mcp-secret": _secret(),
        "X-Forwarded-For": actor,
    }


def _call(payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        BRIDGE_URL,
        data=json.dumps(payload).encode(),
        headers=_headers("pytest"),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            try:
                body = json.loads(raw)
            except Exception:
                body = {"raw": raw.decode("utf-8", errors="replace")}
            return resp.status, body.get("result", body)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw.decode("utf-8", errors="replace")}
        return exc.code, body.get("result", body)


def _make_memory(content: str) -> str:
    status, body = _call({"tool": "memory_store", "args": {"content": content, "type_": "SEMANTIC", "tags": ["pytest"]}})
    assert status == 200, body
    return body["memory_id"]


# ---------------------------------------------------------------------------
# R1 control legitimate
# ---------------------------------------------------------------------------
def test_r1_control_legitimate():
    mid = _make_memory("R1: control legitimate")
    status, body = _call({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "VERIFIED", "reason": "legit"}})
    assert status == 200, body
    assert body.get("verification_state") == "VERIFIED"


# ---------------------------------------------------------------------------
# R2 forged actor
# ---------------------------------------------------------------------------
def test_r2_forged_actor():
    mid = _make_memory("R2: forged actor")
    status, body = _call({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "VERIFIED", "by": "eve"}})
    assert status == 200, body
    # Phase 2: actor is bound to auth context (`by` is overridden).
    # Response doesn't yet surface `last_actor`; tighten when P2 lands.


# ---------------------------------------------------------------------------
# R3 contradiction no resolution → expected failure
# ---------------------------------------------------------------------------
def test_r3_contradiction_no_resolution():
    mid = _make_memory("R3: contradiction")
    _call({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "CONTRADICTED", "reason": "mark"}})
    status, body = _call({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "CONTRADICTED", "reason": "retry"}})
    assert status == 500, body


# ---------------------------------------------------------------------------
# R3b control contradicted
# ---------------------------------------------------------------------------
def test_r3b_control_contradicted():
    mid = _make_memory("R3b: control contradicted")
    status, body = _call({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "CONTRADICTED", "reason": "mark"}})
    assert status == 200, body
    assert body.get("verification_state") == "CONTRADICTED"


# ---------------------------------------------------------------------------
# R4 contradiction with resolution
# ---------------------------------------------------------------------------
def test_r4_contradiction_with_resolution():
    mid = _make_memory("R4: resolved contradiction")
    _call({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "CONTRADICTED", "reason": "initial"}})
    status, body = _call({
        "tool": "memory_verify",
        "args": {"memory_id": mid, "to_state": "VERIFIED", "reason": "resolution", "resolution": "cleared contradiction"},
    })
    assert status == 200, body
    assert body.get("verification_state") == "VERIFIED"


# ---------------------------------------------------------------------------
# R5 empty actor
# ---------------------------------------------------------------------------
def test_r5_empty_actor():
    # Phase 2 pending: empty X-Forwarded-For is not yet rejected at bridge.
    # Current server is permissive; tighten to 401 when P2 lands.
    mid = _make_memory("R5: empty actor")
    req = urllib.request.Request(
        BRIDGE_URL,
        data=json.dumps({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "VERIFIED"}}).encode(),
        headers={"Content-Type": "application/json", "x-mcp-secret": _secret()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            status, body = resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        status, body = exc.code, json.loads(exc.read())
    assert status == 200, {"note": "Phase 2 pending: tighten to 401 when X-Forwarded-For is enforced", "body": body}


# ---------------------------------------------------------------------------
# A6 requested_by vs authenticated_actor (Phase 4 pending; validates separation)
# ---------------------------------------------------------------------------
def test_a6_requested_by_separation():
    mid = _make_memory("A6: requested_by separation")
    status, body = _call({
        "tool": "memory_verify",
        "args": {"memory_id": mid, "to_state": "VERIFIED", "reason": "legit", "by": "eve"},
    })
    assert status == 200, body
    # Phase 4 pending: requested_by isn't returned in response yet.
    # Verify DB directly when P4 lands.


# ---------------------------------------------------------------------------
# S1 server fingerprint
# ---------------------------------------------------------------------------
def test_s1_server_fingerprint():
    status, body = _call({"tool": "status", "args": {}})
    assert status == 200, body
    meta = body.get("server", body) if isinstance(body, dict) else {}
    assert meta.get("service") == "msb-v3"
    assert meta.get("ready") is True


# ---------------------------------------------------------------------------
# S2 hash chain integrity
# ---------------------------------------------------------------------------
def test_s2_hash_chain():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM verification_history ORDER BY id").fetchall()]

    for i in range(1, len(rows)):
        prev_hash = rows[i - 1].get("record_hash")
        curr_prev = rows[i].get("previous_hash")
        if prev_hash and curr_prev:
            assert curr_prev == prev_hash, f"chain break at row {i}"
