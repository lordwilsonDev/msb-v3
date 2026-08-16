"""Tests for /business registry-of-truth router."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app
from msb_v3.core.config import settings


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MSB_TRUTH_DIR", str(tmp_path / "truth"))
    # Phase 1 hardening: /business write routes are operator-gated
    # (fail-closed 503 until MSB_OPERATOR_TOKEN is set). Authenticate for
    # the write tests; the auth test below toggles it.
    monkeypatch.setattr(settings, "operator_token", "test-operator-token")
    app = create_app()
    return TestClient(app, headers={"Authorization": "Bearer test-operator-token"})


def test_register_and_retrieve(client):
    r = client.post("/business/register", json={"domain": "ai", "claim": "Ralph Loop is deterministic"})
    assert r.status_code == 200
    data = r.json()
    entity_id = data["id"]
    assert data["ok"] is True

    r2 = client.get(f"/business/retrieve/{entity_id}")
    assert r2.status_code == 200
    assert r2.json()["data"]["domain"] == "ai"


def test_register_duplicate_409(client):
    client.post("/business/register", json={"domain": "test"})
    r = client.post("/business/register", json={"domain": "test"})
    assert r.status_code == 409


def test_retrieve_missing_404(client):
    r = client.get("/business/retrieve/does-not-exist")
    assert r.status_code == 404


def test_list_empty(client):
    r = client.get("/business/list")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_purge(client):
    r = client.post("/business/register", json={"domain": "purge-test"})
    entity_id = r.json()["id"]
    r2 = client.delete(f"/business/purge/{entity_id}")
    assert r2.status_code == 200
    r3 = client.get(f"/business/retrieve/{entity_id}")
    assert r3.status_code == 404


def test_writes_fail_closed_without_operator_token(client, monkeypatch):
    """Phase 1: an unauthenticated write to the registry-of-truth must be
    refused (503 until the operator token is configured) — reads stay open."""
    monkeypatch.setattr(settings, "operator_token", "")
    r = client.post("/business/register", json={"domain": "x"})
    assert r.status_code == 503
    r2 = client.post(
        "/business/register",
        json={"domain": "x"},
        headers={"Authorization": "Bearer "},
    )
    assert r2.status_code == 503
    # reads stay open
    assert client.get("/business/list").status_code == 200


def test_checksum_integrity(client):
    payload = {"domain": "integrity", "claim": "unchanged"}
    r = client.post("/business/register", json=payload)
    entity_id = r.json()["id"]
    path = __import__("pathlib").Path(__import__("os").getenv("MSB_TRUTH_DIR")) / f"{entity_id}.json"
    data = __import__("json").loads(path.read_text())
    data["claim"] = "tampered"
    path.write_text(__import__("json").dumps(data, indent=2))
    r2 = client.get(f"/business/retrieve/{entity_id}")
    assert r2.status_code == 409
