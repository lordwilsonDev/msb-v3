"""Tests for the /agent control surface — the Handle-this slice over HTTP."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.api.app import create_app  # noqa: E402
from msb_v3.core.config import settings  # noqa: E402


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _open_operator(monkeypatch: pytest.MonkeyPatch, token: str = "tok") -> None:
    """Configure the operator token both ways: settings is a module-level
    object built at import, so setenv alone would be invisible to it."""
    monkeypatch.setenv("MSB_OPERATOR_TOKEN", token)
    monkeypatch.setattr(settings, "operator_token", token)


def test_agent_handle_requires_operator_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The slice runs tools against the vault tenant — the control surface
    must be fail-closed: 503 without a configured token, 401 on mismatch."""
    monkeypatch.delenv("MSB_OPERATOR_TOKEN", raising=False)
    monkeypatch.setattr(settings, "operator_token", "")
    client = TestClient(create_app())
    r = client.post("/agent/handle", json={"request": "hi"})
    assert r.status_code == 503
    r = client.post("/agent/handle", json={"request": "hi"}, headers=_auth_headers("wrong"))
    assert r.status_code == 503  # token empty -> still closed


def test_agent_handle_rejects_bad_request_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _open_operator(monkeypatch)
    client = TestClient(create_app())

    r = client.post("/agent/handle", json={"request": "  "}, headers=_auth_headers("tok"))
    assert r.status_code == 422
    r = client.post("/agent/handle", json={"privacy": "nope"}, headers=_auth_headers("tok"))
    assert r.status_code == 422
    r = client.post("/agent/handle", json={"request": "x"}, headers=_auth_headers("wrong"))
    assert r.status_code == 401


def test_agent_handle_runs_slice_and_forwards_privacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The privacy override is forwarded to handle() — a public (False) and
    an omitted (None, intent decides) request both land correctly."""
    _open_operator(monkeypatch)
    client = TestClient(create_app())

    seen: dict = {}

    async def _stub_handle(request, **kwargs):
        from msb_v3.agent.handle import HandleResult

        seen.update(kwargs)
        return HandleResult(ok=True, run_id="r1", verdict="PASS", deterministic_hash="h")

    import msb_v3.api.agent as agent_mod

    monkeypatch.setattr(agent_mod, "handle", _stub_handle)

    r = client.post(
        "/agent/handle",
        json={"request": "summarize the public vault topic", "privacy": False},
        headers=_auth_headers("tok"),
    )
    assert r.status_code == 200
    assert r.json()["verdict"] == "PASS"
    assert seen.get("privacy") is False

    r = client.post(
        "/agent/handle",
        json={"request": "summarize the vault topic"},
        headers=_auth_headers("tok"),
    )
    assert r.status_code == 200
    assert seen.get("privacy") is None  # default: the intent decides


_FRESH_PROCESS = r"""
import sys
sys.path.insert(0, "@@SRC@@")

# A fresh interpreter == the live server process: importing the app registers
# the router counter before any decision; an open seam + public plan routes
# frontier and increments it. Assertions run here, so failures raise in this
# process and surface as non-zero exit.
from prometheus_client import REGISTRY
from msb_v3.fabric.model_router import ModelRouter

# prometheus_client Counter names carry the _total suffix in the registry
# (the family is msb_v3_router_decisions, exported as ..._total).
names = {m.name for m in REGISTRY.collect()}
assert "msb_v3_router_decisions" in names, "counter not registered at startup"

d = ModelRouter(available=True).decide("plan", privacy_scoped=False)
assert d.tier == "frontier", d
assert d.score > 0.5, d

# The /agent surface is mounted: an unauthenticated POST answers 503
# (fail-closed), not 404 (not mounted).
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app

app = create_app()
r = TestClient(app).post("/agent/handle", json={"request": "x"})
assert r.status_code == 503, r.status_code  # mounted + token unset -> closed

value = REGISTRY.get_sample_value(
    "msb_v3_router_decisions_total",
    {"task_kind": "plan", "tier": "frontier", "cause": "tier-default"},
)
assert value is not None and value >= 1, value

# Privacy floor still holds on the same open seam.
dp = ModelRouter(available=True).decide("plan", privacy_scoped=True)
assert dp.tier == "local", dp
print("FRESH-PROCESS-OK tier=%s score=%s counter=%s" % (d.tier, d.score, value))
""".replace("@@SRC@@", str(SRC))


def test_fresh_server_process_registers_counter_and_routes_frontier() -> None:
    """Run the live-test assertions in a clean interpreter, exactly like the
    server process: counter registered at startup, public plan -> frontier on
    an open seam, decision counted, /agent mounted."""
    proc = subprocess.run(
        [sys.executable, "-c", _FRESH_PROCESS],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"subprocess failed:\n{proc.stdout}\n{proc.stderr}"
    assert "FRESH-PROCESS-OK" in proc.stdout
