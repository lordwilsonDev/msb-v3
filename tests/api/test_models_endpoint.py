"""Smoke test for /models endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from msb_v3.api.app import create_app


def test_models_endpoint():
    client = TestClient(create_app())
    r = client.get("/models/", follow_redirects=False)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 2
    backends = {m["backend"] for m in data}
    assert backends == {"ollama", "llamacpp"}


def test_models_switch():
    client = TestClient(create_app())
    r = client.post("/models/switch", json={"backend": "llamacpp"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
