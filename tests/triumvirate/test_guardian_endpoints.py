"""Tests for Triumvirate Phase 3 — Guardian Protocol endpoints."""
from __future__ import annotations

import os
import tempfile

import httpx
import pytest

BASE = os.environ.get("MSB_BASE_URL", "http://127.0.0.1:8766")

# Server-integration: asserts against a running msb-v3 on MSB_BASE_URL / :8766.
# Tier: integration (PRODUCTION-CLOSURE-001 P1) — not part of the hermetic core.
pytestmark = pytest.mark.integration


def _get(path, expected=200):
    with httpx.Client(timeout=10) as client:
        r = client.get(f"{BASE}{path}")
    assert r.status_code == expected, f"GET {path} -> {r.status_code}"


def _post(path, body, expected=200):
    with httpx.Client(timeout=10) as client:
        r = client.post(f"{BASE}{path}", json=body, headers={"content-type": "application/json"})
    assert r.status_code == expected, f"POST {path} -> {r.status_code}: {r.text}"


def test_guardian_scan_blocks_bad_script():
    _post("/triumvirate/guardian/scan", {"script": "import os\nos.system('id')\n"})


def test_guardian_scan_allows_clean_script():
    _post("/triumvirate/guardian/scan", {"script": "print('ok')"})


def test_guardian_sbom_round_trip():
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as tf:
        tf.write(b"x=1\n")
        path = tf.name
    try:
        _post("/triumvirate/guardian/sbom/register", {"server_id": "srv-a", "path": path})
        _get("/triumvirate/guardian/sbom/srv-a")
    finally:
        os.unlink(path)


def test_guardian_least_privilege_allows():
    _post("/triumvirate/guardian/least-privilege", {"agent_token": "sub-agent-token-1", "required_scope": "read"})


def test_guardian_least_privilege_blocks():
    _post("/triumvirate/guardian/least-privilege", {"agent_token": "sub-agent-token-1", "required_scope": "admin"}, expected=200)


def test_guardian_poison_pill_cycle():
    """Arm -> detonate, then re-arm so the live server never leaves the
    repo's committed poison_pill.json kill-switched (audit SMI-017 #6: a
    locked pill committed to the tree breaks least-privilege tests on every
    fresh checkout)."""
    _post("/triumvirate/guardian/poison-pill/arm", {})
    _post("/triumvirate/guardian/poison-pill/detonate", {})
    _post("/triumvirate/guardian/poison-pill/arm", {})
