"""Integration test for Triumvirate plan→lock→verify lifecycle."""
from __future__ import annotations

import os

import httpx
import pytest

BASE = os.environ.get("MSB_BASE_URL", "http://127.0.0.1:8766")

# Server-integration: asserts against a running msb-v3 on MSB_BASE_URL / :8766.
# Tier: integration (PRODUCTION-CLOSURE-001 P1) — not part of the hermetic core.
pytestmark = pytest.mark.integration


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
    # xdist_group pins this test to a single worker, which is the actual
    # fix for concurrent-modification races — it was added in the same
    # commit (27f780c) as a redundant manual skip that fired on ANY xdist
    # worker, unconditionally, defeating the whole point of the group
    # (removed 2026-09-11: this test never actually ran under xdist since
    # that commit, and pytest-xdist isn't even invoked with -n anywhere in
    # this repo's CI/Makefile today, so the skip was pure dead weight).
    # NOTE: tests/test_harness.py and tests/triumvirate/test_metrics.py also
    # hit these same shared-state endpoints and are NOT in this xdist_group —
    # if this suite ever does run with real parallel workers (-n), those two
    # files need the same xdist_group("triumvirate") tag to be fully race-safe.
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
