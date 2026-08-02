"""Regression: /system/health must not 500 (stale Database import, fixed)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from msb_v3.api.app import create_app
from msb_v3.local_ai.ollama import LocalAIClient


def test_system_health_returns_200(monkeypatch):
    monkeypatch.setattr(
        LocalAIClient, "generate",
        lambda self, *a, **k: (_ for _ in ()).throw(ConnectionError("ollama down")),
    )
    client = TestClient(create_app())
    resp = client.get("/system/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["app"] == "ok"
    assert body["db"] == "ok"
    assert body["ollama"].startswith("error:")
    assert body["status"] == "degraded"


def test_system_health_healthy_when_all_checks_pass(monkeypatch):
    monkeypatch.setattr(LocalAIClient, "generate", lambda self, *a, **k: "ok")
    client = TestClient(create_app())
    body = client.get("/system/health").json()
    assert body["ollama"] == "ok"
    assert body["status"] == "healthy"
