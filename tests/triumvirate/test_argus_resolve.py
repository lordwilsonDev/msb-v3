"""Tests for Triumvirate Phase 4 — Argus mulch resolve endpoint."""
from __future__ import annotations

import os

import httpx

BASE = os.environ.get("MSB_BASE_URL", "http://127.0.0.1:8766")


def _post(path, body=None, expected=200):
    with httpx.Client(timeout=10.0) as client:
        r = client.post(f"{BASE}{path}", json=body or {}, headers={"content-type": "application/json"})
    assert r.status_code == expected, f"POST {path} -> {r.status_code}: {r.text}"
    return r.json()


def _get(path, expected=200):
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{BASE}{path}")
    assert r.status_code == expected, f"GET {path} -> {r.status_code}"
    return r.json()


def test_argus_mulch_resolve():
    _post("/triumvirate/argus/audit", {})
    mulch = _get("/triumvirate/argus/mulch")
    rows = mulch.get("rows", [])
    if not rows:
        return
    row_id = rows[0]["id"]
    result = _post(f"/triumvirate/argus/mulch/{row_id}/resolve", {})
    assert result.get("ok") is True
    assert result.get("id") == row_id
