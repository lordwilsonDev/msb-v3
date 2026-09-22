"""GET /ops/background: operator-gated, read-only snapshot route."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from msb_v3.api import ops_background
from msb_v3.api.app import create_app
from msb_v3.core.config import settings
from msb_v3.ops.background import SnapshotCache


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MSB_OPERATOR_TOKEN", "tok")
    monkeypatch.setattr(settings, "operator_token", "tok")
    monkeypatch.setattr(ops_background, "_cache", SnapshotCache(ttl_s=0))

    async def fake_build():
        return {"generated_at": "2026-09-22T12:00:00+00:00",
                "subsystems": {"cron": {"state": "ok", "detail": {}, "error": None}}}

    monkeypatch.setattr(ops_background, "build_snapshot", fake_build)
    return TestClient(create_app())


def test_requires_operator_token(client: TestClient) -> None:
    assert client.get("/ops/background").status_code == 401
    assert client.get("/ops/background", headers=_auth("wrong")).status_code == 401


def test_closed_when_no_token_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSB_OPERATOR_TOKEN", raising=False)
    monkeypatch.setattr(settings, "operator_token", "")
    r = TestClient(create_app()).get("/ops/background", headers=_auth("x"))
    assert r.status_code == 503


def test_returns_snapshot(client: TestClient) -> None:
    r = client.get("/ops/background", headers=_auth("tok"))
    assert r.status_code == 200
    assert r.json()["subsystems"]["cron"]["state"] == "ok"


def test_no_write_methods(client: TestClient) -> None:
    for method in ("post", "put", "patch", "delete"):
        r = getattr(client, method)("/ops/background", headers=_auth("tok"))
        assert r.status_code == 405, method
