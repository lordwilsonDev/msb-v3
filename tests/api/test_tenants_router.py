"""/tenants surface — intended paths + operator-gated writes.

Phase 1 hardening (forensic-build-audit 2026-08-15): the tenant router
mounted at prefix "/tenants" used to repeat "tenants" in every route path
(``/tenants/tenants/register``) — a latent double-prefix that made the
intended surface unreachable. These tests pin the intended paths and the
operator gate on the write routes (reads stay open).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app
from msb_v3.core.config import settings


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MSB_TENANT_DIR", str(tmp_path / "tenants"))
    monkeypatch.setattr(settings, "operator_token", "test-operator-token")
    return TestClient(create_app(), headers={"Authorization": "Bearer test-operator-token"})


def test_intended_paths_mounted(client):
    """/tenants/* with no double prefix (regression pin)."""
    paths = set(client.app.openapi()["paths"])
    assert "/tenants" in paths
    assert "/tenants/register" in paths
    assert "/tenants/{tenant_id}" in paths
    assert not any("tenants/tenants" in p for p in paths)


def test_register_write_requires_operator(client, monkeypatch):
    monkeypatch.setattr(settings, "operator_token", "")
    r = client.post("/tenants/register", json={"id": "acme"})
    assert r.status_code == 503  # fail-closed until the token is configured


def test_register_and_read_round_trip(client):
    r = client.post("/tenants/register", json={"id": "acme", "name": "Acme"})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "acme"

    r2 = client.get("/tenants/acme")
    assert r2.status_code == 200
    assert r2.json()["tenant"]["name"] == "Acme"

    r3 = client.get("/tenants")
    assert r3.status_code == 200
    assert r3.json()["count"] == 1


def test_reads_stay_open_without_token(client, monkeypatch):
    client.post("/tenants/register", json={"id": "acme"})
    monkeypatch.setattr(settings, "operator_token", "")
    assert client.get("/tenants").status_code == 200
    assert client.get("/tenants/acme").status_code == 200


def test_traversal_still_rejected(client):
    """The containment guard from SMI-017 #3 still holds on the fixed paths.
    Starlette collapses raw ``..`` segments before routing (404 — the
    traversal never reaches the handler); an encoded form that survives to
    the handler gets the 400 containment rejection. Both are refusals."""
    r = client.get("/tenants/..%2F..%2Fetc")
    assert r.status_code == 404
    # encoded form that reaches the handler must be contained
    r2 = client.get("/tenants/%2E%2E%2F%2E%2E%2Fetc")
    assert r2.status_code in (400, 404)
