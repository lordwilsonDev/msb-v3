"""Tests for sovereign core API."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def test_app_imports():
    spec = importlib.util.find_spec("msb_v3.api.app")
    assert spec is not None


def test_chat_endpoint():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    resp = client.post("/chat", json={"query": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["payload"]["query"] == "hello"


def test_health_endpoints():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r1 = client.get("/health")
    assert r1.status_code == 200
    assert r1.json()["service"] == "msb-v3"
    r2 = client.get("/ready")
    assert r2.status_code in (200, 503)


def test_metrics_endpoint():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.get("/metrics/")
    assert r.status_code == 200
    assert "prometheus" in r.json()


def test_studio_dashboard_returns_html():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text


def test_system_info():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.get("/system/info")
    assert r.status_code == 200
    assert r.json()["model"] == "qwen3:latest"
