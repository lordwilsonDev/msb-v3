"""P12 race/TOCTOU: concurrent state transitions.

Verifies that two simultaneous verification requests for the same memory
either serialize correctly or produce an error — never corrupt state.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading

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
# The server under test is whatever MSB_BASE_URL names, like every other live
# test in this suite. Hardcoding :8766 meant that on an operator's machine this
# file silently tested (and wrote rows into) the LIVE deployment instead of the
# standby the run had started.
BASE_URL = os.environ.get("MSB_BASE_URL", "http://127.0.0.1:8766").rstrip("/")
BRIDGE_URL = f"{BASE_URL}/mcp/proxy"
DB_PATH = os.path.join(REPO, "data", "memory_fabric", "memory.db")


@pytest.fixture(autouse=True)
def _point_at_the_server_fabric(
    server_fabric_db_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read the fabric of the server under test (see conftest)."""
    monkeypatch.setattr(sys.modules[__name__], "DB_PATH", server_fabric_db_path)

_lock = threading.Lock()
_errors: list[dict] = []


def _headers(actor: str) -> dict:
    return {
        "Content-Type": "application/json",
        "x-mcp-secret": SECRET,
        "x-forwarded-for": actor,
    }


def _call(payload: dict) -> tuple[int, dict]:
    req = requests.post(BRIDGE_URL, json=payload, headers=_headers("p12"), timeout=10)
    try:
        body = req.json()
    except Exception:
        body = {"raw": req.text[:200]}
    return req.status_code, body


def _make_memory(content: str) -> str:
    status, body = _call({"tool": "memory_store", "args": {"content": content, "type_": "SEMANTIC", "tags": ["p12"]}})
    assert status == 200, body
    ret = body.get("result", body)
    if isinstance(ret, dict):
        return ret["memory_id"]
    return getattr(ret, "memory_id", ret)


def _verify(mid: str, state: str, label: str) -> tuple[int, dict]:
    return _call({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": state, "reason": f"p12 {label}"}})


def _history(mid: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT * FROM verification_history WHERE memory_id = ? ORDER BY id", (mid,)
        ).fetchall()]


# ---------------------------------------------------------------------------
# T1: two concurrent VERIFY calls for same memory
# ---------------------------------------------------------------------------
def test_t1_concurrent_same_state():
    mid = _make_memory("P12 T1 concurrent same-state")
    # Step to VERIFIED once so we start from a known state
    _verify(mid, "VERIFIED", "baseline")

    results = {}
    barrier = threading.Barrier(2)

    def worker(label: str):
        barrier.wait()  # synchronize start
        s, b = _verify(mid, "CONTRADICTED", label)
        results[label] = (s, b)

    t1 = threading.Thread(target=worker, args=("t1a",), daemon=True)
    t2 = threading.Thread(target=worker, args=("t1b",), daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    # Both should return 200 (server serializes) or one 200 + one 500/422
    # But never both corrupting the state machine
    for label, (s, b) in results.items():
        assert s in (200, 400, 422, 500), f"{label}: unexpected status {s}: {b}"

    # Final state must be a valid terminal for the attempted transition set
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT verification_state FROM memory_items WHERE memory_id = ?", (mid,)).fetchone()
    final = row[0] if row else None
    assert final in ("VERIFIED", "CONTRADICTED"), f"corrupted final state: {final}"


# ---------------------------------------------------------------------------
# T2: two threads try opposite transitions simultaneously
# ---------------------------------------------------------------------------
def test_t2_opposite_states_serialize():
    mid = _make_memory("P12 T2 opposite states")
    results = {}
    barrier = threading.Barrier(2)

    def worker(label: str, state: str):
        barrier.wait()
        s, b = _verify(mid, state, label)
        results[label] = (s, b)

    t1 = threading.Thread(target=worker, args=("t2a", "VERIFIED"), daemon=True)
    t2 = threading.Thread(target=worker, args=("t2b", "CONTRADICTED"), daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    # At least one request must succeed (200)
    successes = [label for label, (s, _) in results.items() if s == 200]
    assert len(successes) >= 1, "both ops failed — possible double-reject"

    # Count history rows — must equal number of successful ops
    history = _history(mid)
    assert len(history) == len(successes), f"history len {len(history)} != successes {len(successes)}"

    # State must be one of the two requested states
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT verification_state FROM memory_items WHERE memory_id = ?", (mid,)).fetchone()
    assert row and row[0] in ("VERIFIED", "CONTRADICTED")


# ---------------------------------------------------------------------------
# T3: double-VERIFIED from UNVERIFIED should not create duplicate history
# ---------------------------------------------------------------------------
def test_t3_double_verify_no_double_history():
    mid = _make_memory("P12 T3 double verify")
    # Step to VERIFIED once to establish baseline
    _verify(mid, "VERIFIED", "pre")

    # Now fire two VERIFIED→VERIFIED attempts concurrently
    results = {}
    barrier = threading.Barrier(2)

    def worker(label: str):
        barrier.wait()
        s, b = _verify(mid, "VERIFIED", label)
        results[label] = (s, b)

    t1 = threading.Thread(target=worker, args=("t3a",), daemon=True)
    t2 = threading.Thread(target=worker, args=("t3b",), daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    history = _history(mid)
    # Only 1 history entry for the initial VERIFIED; re-verify should not append duplicate
    assert len(history) == 1, f"expected 1 history row, got {len(history)}"
