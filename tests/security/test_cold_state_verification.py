"""P11 cold-state verification: fingerprint matches after server restart.

Strategy:
1. Create memory, verify it, capture history `record_hash` before restart.
2. Restart the server process (cold state).
3. Query history — same row should still be there with matching hash.

Also verifies that a fresh server doesn't re-create or re-verify the memory.
"""

from __future__ import annotations

import os
import sys
import sqlite3
import subprocess
import time
import requests

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "src"))

import msb_v3.api.mcp_bridge as bridge  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(REPO, ".env"))
SECRET = os.environ.get("MCP_BRIDGE_SECRET", "") or getattr(bridge, "_MCP_BRIDGE_SECRET", "")
DB_PATH = os.path.join(REPO, "data", "memory_fabric", "memory.db")
BRIDGE_URL = "http://127.0.0.1:8766/mcp/proxy"


def _headers(actor: str) -> dict:
    return {
        "Content-Type": "application/json",
        "x-mcp-secret": SECRET,
        "x-forwarded-for": actor,
    }


def _call(payload: dict) -> tuple[int, dict]:
    req = requests.post(BRIDGE_URL, json=payload, headers=_headers("p11"), timeout=10)
    try:
        body = req.json()
    except Exception:
        body = {"raw": req.text[:200]}
    return req.status_code, body


def _make_memory(content: str) -> str:
    status, body = _call({"tool": "memory_store", "args": {"content": content, "type_": "SEMANTIC", "tags": ["p11"]}})
    assert status == 200, body
    ret = body.get("result", body)
    if isinstance(ret, dict):
        return ret["memory_id"]
    return getattr(ret, "memory_id", ret)


def _verify(mid: str) -> tuple[int, dict]:
    return _call({"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "VERIFIED", "reason": "p11 baseline"}})


def _history(mid: str) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT * FROM verification_history WHERE memory_id = ? ORDER BY id", (mid,)
        ).fetchall()]


def _server_pid() -> int | None:
    try:
        out = subprocess.check_output(["lsof", "-ti", "tcp:8766"], text=True).strip()
        return int(out.splitlines()[0]) if out else None
    except Exception:
        return None


def _restart_server() -> None:
    pid = _server_pid()
    if pid:
        os.kill(pid, 9)
        time.sleep(1)
    subprocess.Popen(
        ["bash", "scripts/run.sh"],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        try:
            if requests.get("http://127.0.0.1:8766/status", timeout=1).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("server did not come back after restart")


# ---------------------------------------------------------------------------
# P11-1: fingerprint survives restart
# ---------------------------------------------------------------------------
def test_p11_fingerprint_survives_restart():
    mid = _make_memory("P11 fingerprint after restart")
    _verify(mid)  # first VERIFIED
    before = _history(mid)
    assert len(before) >= 1
    fp_before = before[-1]["record_hash"]

    _restart_server()
    time.sleep(1)

    after = _history(mid)
    assert len(after) == len(before), "history length changed after restart"
    assert after[-1]["record_hash"] == fp_before, "fingerprint changed after restart"

    # Re-read memory state via direct SQLite — no extra verify needed
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT verification_state FROM memory_items WHERE memory_id = ?", (mid,)).fetchone()
        assert row and row[0] == "VERIFIED", f"state after restart: {row}"
