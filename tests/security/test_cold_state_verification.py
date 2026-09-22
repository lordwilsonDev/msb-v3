"""P11 cold-state verification: fingerprint matches after server restart.

Strategy:
1. Create memory, verify it, capture history `record_hash` before restart.
2. Restart the server process (cold state).
3. Query history — same row should still be there with matching hash.

Also verifies that a fresh server doesn't re-create or re-verify the memory.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit

import pytest
import requests

pytestmark = pytest.mark.integration

# MSB_HOME is the same root override `core/config.py` honours, so a run pointed at
# a staged or standby instance reads that instance's DB, not the live one's.
REPO = os.environ.get("MSB_HOME") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, os.path.join(REPO, "src"))

from dotenv import load_dotenv  # noqa: E402

import msb_v3.api.mcp_bridge as bridge  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))
SECRET = os.environ.get("MCP_BRIDGE_SECRET", "") or getattr(bridge, "_MCP_BRIDGE_SECRET", "")
if not SECRET:
    # Portability gate stages a copy with .env deliberately excluded
    # (secrets must never be copied) — these tests need the real running
    # server's secret and cannot pass against an empty one.
    pytestmark = [pytest.mark.integration, pytest.mark.skip(reason="MCP_BRIDGE_SECRET unavailable")]
# `scripts/ci-runtime.sh` redirects the server's stores but deliberately does
# NOT export those redirects — exporting MSB_DB_PATH would redirect the pytest
# process's own Settings too, which is the regression that comment records. It
# records them in $CI_RUNTIME_DIR/server.env instead, which is what this reads.
# Reconstructing the layout from CI_RUNTIME_DIR would be wrong wherever a caller
# overrides it: verify-release.sh points CI_SERVER_RESEARCH at the clone's
# seeded fixtures. Without this the assertions below read the deployment's real
# DBs while the standby writes temp ones — green, and about nothing.
_RUNTIME_DIR = os.environ.get("CI_RUNTIME_DIR")


def _store_env() -> dict[str, str]:
    """The stores the run-scoped server was actually started with."""
    path = os.path.join(_RUNTIME_DIR, "server.env") if _RUNTIME_DIR else None
    values: dict[str, str] = {}
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                key, sep, value = line.rstrip("\n").partition("=")
                if sep:
                    values[key] = value
    return values


_STORES = _store_env()
_MF_DB = (
    os.environ.get("MSB_MEMORY_FABRIC_DB_PATH")
    or _STORES.get("MSB_MEMORY_FABRIC_DB_PATH")
    or "data/memory_fabric/memory.db"
)
DB_PATH = _MF_DB if os.path.isabs(_MF_DB) else os.path.join(REPO, _MF_DB)

# The target is configurable, like every other integration test in this suite
# (`tests/test_harness.py` reads MSB_BASE_URL the same way). It used to be
# hardcoded to :8766, which made this file the one test that could not be aimed
# at a throwaway instance.
BASE_URL = os.environ.get("MSB_BASE_URL", "http://127.0.0.1:8766").rstrip("/")
_TARGET = urlsplit(BASE_URL)
TARGET_PORT = _TARGET.port or 8766
BRIDGE_URL = f"{BASE_URL}/mcp/proxy"

# Restarting the target means SIGKILLing whatever owns its port (see
# `_restart_server`) and re-spawning `python -m msb_v3` against this checkout.
# When the target is a standby the suite owns, that is correct. When it is the
# default port it is the operator's live service: killing it mid-suite is the
# documented reason this whole tier is deselected by default, and a respawn that
# fails leaves the service DOWN rather than merely unverified. So the kill needs
# either a non-default target or an explicit opt-in.
_ALLOW_RESTART_LIVE = os.environ.get("MSB_ALLOW_RESTART_LIVE") == "1"


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
        out = subprocess.check_output(["lsof", "-ti", f"tcp:{TARGET_PORT}"], text=True).strip()
        return int(out.splitlines()[0]) if out else None
    except Exception:
        return None


def _restart_server() -> None:
    pid = _server_pid()
    if pid:
        os.kill(pid, 9)
        time.sleep(1)
    # Spawn the server the same way CI boots it (plain `python -m msb_v3`),
    # NOT scripts/run.sh: run.sh hardcodes a macOS-only miniforge python
    # path as its fallback, which does not exist on hosted ubuntu runners —
    # the restart silently failed there and left every later live test with
    # Connection refused (P11 CI regression). Env is inherited, so the
    # MCP_BRIDGE_SECRET loaded from .env / set by CI flows through.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(REPO, "src")
    # Respawn onto the same target the suite is pointed at, against the same
    # root — otherwise an MSB_BASE_URL run kills the standby and brings up the
    # default instance in its place.
    env["MSB_PORT"] = str(TARGET_PORT)
    env["MSB_HOME"] = REPO
    # And onto the same *stores* — read from what the original server was given,
    # not reconstructed from the temp layout, because a caller may override any
    # of them (verify-release.sh overrides the research root with the clone's
    # seeded fixtures). Reconstructing silently came back on an empty
    # research root and failed the two seeded-harness tests.
    env.update(_STORES)
    runtime_dir = _RUNTIME_DIR
    proc = subprocess.Popen(
        [sys.executable, "-m", "msb_v3"],
        cwd=REPO,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Hand the new pid to ci-runtime's cleanup trap, or the respawned server
    # outlives the run as an orphan holding the standby port.
    if runtime_dir:
        try:
            with open(os.path.join(runtime_dir, "server.pid"), "w") as fh:
                fh.write(str(proc.pid))
        except OSError:
            pass
    for _ in range(40):
        try:
            if requests.get(f"{BASE_URL}/status", timeout=1).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("server did not come back after restart")


# ---------------------------------------------------------------------------
# P11-1: fingerprint survives restart
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    TARGET_PORT == 8766 and not _ALLOW_RESTART_LIVE,
    reason=(
        "refusing to SIGKILL whatever owns the default port (a live msb-v3): point the "
        "run at a standby with MSB_BASE_URL, or set MSB_ALLOW_RESTART_LIVE=1 to restart "
        "the default instance deliberately"
    ),
)
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
