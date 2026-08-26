"""Integration test for Triumvirate plan→lock→verify lifecycle."""
from __future__ import annotations

import httpx
import pytest

BASE = "http://127.0.0.1:8766"


def _post(path, body, expected=200):
    with httpx.Client(timeout=10.0) as client:
        r = client.post(f"{BASE}{path}", json=body, headers={"content-type": "application/json"})
    assert r.status_code == expected, f"POST {path} -> {r.status_code}: {r.text}"
    return r.json()


def _get(path, expected=200):
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{BASE}{path}")
    assert r.status_code == expected, f"GET {path} -> {r.status_code}"
    return r.json()


@pytest.mark.xdist_group("triumvirate")
def test_triumvirate_plan_lock_verify_cycle():
    goal = "sovereign cluster deploy"
    plan = _post("/triumvirate/plan", {"goal": goal})
    assert plan["goal"] == goal
    assert "slug" in plan

    _post("/triumvirate/status/lock", {"goal": goal})

    status = _get("/triumvirate/status")
    assert status["goal"] == goal
    assert status["current_phase"] == "locked"

    verify = _get("/triumvirate/status/verify")
    assert verify["valid"] is True
    assert verify["scope_hash"] == status["scope_hash"]
