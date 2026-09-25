"""Regression / security suite for memory_verify (Phase 6 hardening).

Runs against MCP bridge by default; direct fabric path covered separately in
``tests/security/test_memory_verify_direct.py``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.integration

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "src"))

# The server under test is whatever MSB_BASE_URL names, like every other live
# test in this suite. Hardcoding :8766 meant that on an operator's machine this
# file silently tested (and wrote rows into) the LIVE deployment instead of the
# standby the run had started — and under a port collision it did so without
# saying anything.
BASE_URL = os.environ.get("MSB_BASE_URL", "http://127.0.0.1:8766").rstrip("/")
BRIDGE_URL = f"{BASE_URL}/mcp/proxy"
# Rebound per test by `_point_at_the_server_fabric` to the fabric of the server
# above, not to whatever happens to sit in the checkout's data/ directory.
DB_PATH = os.path.join(REPO, "data", "memory_fabric", "memory.db")


@pytest.fixture(autouse=True)
def _point_at_the_server_fabric(
    server_fabric_db_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read the fabric of the server under test (see conftest)."""
    monkeypatch.setattr(sys.modules[__name__], "DB_PATH", server_fabric_db_path)


def _secret() -> str:
    env_path = os.path.join(REPO, ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            if line.startswith("MCP_BRIDGE_SECRET="):
                return line.strip().split("=", 1)[1]
    return os.environ.get("MCP_BRIDGE_SECRET", "")


if not _secret():
    # Portability gate stages a copy with .env deliberately excluded
    # (secrets must never be copied) — these tests need the real running
    # server's secret and cannot pass against an empty one.
    pytestmark = [pytest.mark.integration, pytest.mark.skip(reason="MCP_BRIDGE_SECRET unavailable")]


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
# R3 contradiction no resolution → the retry must not be silently accepted
# ---------------------------------------------------------------------------
def test_r3_contradiction_no_resolution():
    """The retry is a no-op, and the memory does not move.

    This asserted ``status == 500`` until 2026-09-24. What it meant to pin was
    "an unresolved contradiction must not be silently accepted" — but the retry
    failed as an INV-05 *self-loop* (the resolution gate only governs
    CONTRADICTED → VERIFIED), so the assertion also locked in a 500 for every
    duplicate verify through /mcp. The bridge now answers the postcondition
    explicitly (see ``_mf_verify``); the intent is asserted directly instead:
    the state is unchanged and the caller is told nothing happened.
    """
    mid = _make_memory("R3: contradiction")
    _call({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "CONTRADICTED", "reason": "mark"}})
    status, body = _call({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "CONTRADICTED", "reason": "retry"}})
    assert status == 200, body
    assert body.get("no_change") is True
    assert body.get("verification_state") == "CONTRADICTED"
    # Not silently promoted: no resolution was supplied, so VERIFIED is still
    # out of reach.
    status2, body2 = _call({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "VERIFIED", "reason": "no resolution"}})
    assert status2 != 200, body2


# ---------------------------------------------------------------------------
# R3c re-verifying an already-VERIFIED memory is a no-op, not a 500
# ---------------------------------------------------------------------------
def test_r3c_reverify_verified_is_an_explicit_no_op():
    mid = _make_memory("R3c: reverify verified")
    first = _call({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "VERIFIED", "reason": "first"}})
    assert first[0] == 200, first[1]
    assert "no_change" not in first[1]

    again = _call({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "VERIFIED", "reason": "retry"}})
    assert again[0] == 200, again[1]
    assert again[1].get("no_change") is True
    assert again[1].get("verification_state") == "VERIFIED"


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
