"""Tests for the skill router."""
from __future__ import annotations

import os

import httpx
import pytest

BASE = os.environ.get("MSB_BASE_URL", "http://127.0.0.1:8766")

# Server-integration: asserts against a running msb-v3 on MSB_BASE_URL / :8766.
# Tier: integration (PRODUCTION-CLOSURE-001 P1) — not part of the hermetic core.
pytestmark = pytest.mark.integration


def _get(path):
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{BASE}{path}", headers={"accept": "application/json"})
    assert r.status_code == 200, f"GET {path} -> {r.status_code}: {r.text}"
    return r.json()


def test_list_skills():
    data = _get("/skills/")
    assert "skills" in data
    assert data["count"] >= 1
    names = {s["name"] for s in data["skills"]}
    assert "triumvirate-api-patterns" in names


def test_get_skill_by_name():
    data = _get("/skills/triumvirate-api-patterns")
    assert data["name"] == "triumvirate-api-patterns"
    assert "description" in data
    assert "content" in data


def test_get_skill_not_found():
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{BASE}/skills/does-not-exist", headers={"accept": "application/json"})
    assert r.status_code == 404


def test_execute_skill_dispatches():
    with httpx.Client(timeout=10.0) as client:
        r = client.post(
            f"{BASE}/skills/execute",
            json={"skill": "triumvirate-api-patterns", "prompt": "run it", "context": {}},
            headers={"content-type": "application/json"},
        )
    assert r.status_code == 200, f"POST /skills/execute -> {r.status_code}: {r.text}"
    body = r.json()
    assert body["status"] == "dispatched"
    assert body["skill"] == "triumvirate-api-patterns"


def test_execute_skill_requires_name():
    with httpx.Client(timeout=10.0) as client:
        r = client.post(
            f"{BASE}/skills/execute",
            json={"prompt": "run it"},
            headers={"content-type": "application/json"},
        )
    assert r.status_code == 422
