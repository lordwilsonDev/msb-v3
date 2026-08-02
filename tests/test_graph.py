"""Tests for knowledge-graph endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app


@pytest.fixture()
def client():
    return TestClient(create_app())


def test_ingest_creates_nodes(client: TestClient):
    r = client.post("/graph/ingest", json={"session": "kg1", "text": "sovereign stack AI agent", "metadata": {"topic": "demo"}})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["nodes"] >= 3


def test_get_graph(client: TestClient):
    client.post("/graph/ingest", json={"session": "kg2", "text": "Hermes autonomous research"})
    r = client.get("/graph/kg2")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["session"] == "kg2"
    assert len(data["nodes"]) >= 2


def test_top_endpoint(client: TestClient):
    client.post("/graph/ingest", json={"session": "kg3", "text": "AI sovereign stack"})
    r = client.get("/graph/kg3/top")
    assert r.status_code == 200
    assert len(r.json()["top"]) >= 2


def test_list_sessions(client: TestClient):
    r = client.get("/graph")
    assert r.status_code == 200
    assert "sessions" in r.json()
