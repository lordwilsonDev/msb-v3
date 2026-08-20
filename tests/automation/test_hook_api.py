"""Tests for the /hook webhook sense (api/hook.py) — one endpoint every
platform points at; payloads queue into the wake inbox for the resident
agent. Deliberately not operator-gated: the edge is the optional shared
secret + bounded payloads."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.api.app import create_app  # noqa: E402
from msb_v3.core.config import settings  # noqa: E402
from msb_v3.wake.store import WakeStore  # noqa: E402


def test_hook_queues_signal_without_auth(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "automation_hook_secret", "")
    store = WakeStore(db_path=str(tmp_path / "wake.db"))
    monkeypatch.setattr("msb_v3.api.hook.WakeStore", lambda: store)

    client = TestClient(create_app())
    r = client.post("/hook/auto-1", json={"event": "new_lead", "name": "Cleo"})
    assert r.status_code == 200
    body = r.json()
    assert body["queued"] is True
    pending = store.pending()
    assert len(pending) == 1
    assert pending[0]["sender"] == "hook:auto-1"
    assert "new_lead" in pending[0]["text"]


def test_hook_secret_enforced(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "automation_hook_secret", "s3cret")
    store = WakeStore(db_path=str(tmp_path / "wake.db"))
    monkeypatch.setattr("msb_v3.api.hook.WakeStore", lambda: store)

    client = TestClient(create_app())
    assert client.post("/hook/auto-1", json={"x": 1}).status_code == 401
    assert client.post("/hook/auto-1", json={"x": 1}, headers={"x-hook-secret": "wrong"}).status_code == 401
    ok = client.post("/hook/auto-1", json={"x": 1}, headers={"x-hook-secret": "s3cret"})
    assert ok.status_code == 200
    assert len(store.pending()) == 1


def test_hook_rejects_bad_automation_id(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "automation_hook_secret", "")
    store = WakeStore(db_path=str(tmp_path / "wake.db"))
    monkeypatch.setattr("msb_v3.api.hook.WakeStore", lambda: store)
    client = TestClient(create_app())
    assert client.post("/hook/   ", json={"x": 1}).status_code == 422
