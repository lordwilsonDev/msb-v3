"""Tests for the /wake API surface — operator-gated message channel."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.api.app import create_app  # noqa: E402
from msb_v3.core.config import settings  # noqa: E402


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _open_operator(monkeypatch: pytest.MonkeyPatch, token: str = "tok") -> None:
    monkeypatch.setenv("MSB_OPERATOR_TOKEN", token)
    monkeypatch.setattr(settings, "operator_token", token)


def test_wake_requires_operator_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSB_OPERATOR_TOKEN", raising=False)
    monkeypatch.setattr(settings, "operator_token", "")
    client = TestClient(create_app())
    assert client.post("/wake", json={"text": "hi"}, headers=_auth("nope")).status_code == 503
    assert client.get("/wake/outbox", headers=_auth("nope")).status_code == 503


def test_post_message_and_read_outbox(monkeypatch: pytest.MonkeyPatch) -> None:
    _open_operator(monkeypatch)
    client = TestClient(create_app())

    # Validation.
    assert client.post("/wake", json={}, headers=_auth("tok")).status_code == 422
    assert client.post("/wake", json={"text": "  "}, headers=_auth("tok")).status_code == 422

    r = client.post("/wake", json={"text": "resident, check the inbox", "from": "session-2"}, headers=_auth("tok"))
    assert r.status_code == 200
    msg = r.json()["message"]
    assert msg["status"] == "pending"
    assert msg["sender"] == "session-2"

    # The resident agent hasn't run in this test — outbox is empty, inbox pending.
    r = client.get("/wake/status", headers=_auth("tok"))
    assert r.json()["pending"] == 1
    assert r.json()["schedule"] == "*/5 * * * *"


def test_wake_cycle_via_cron_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """The full loop: post → wake-agent action → outbox reply. Uses a stub
    turn by monkeypatching the runner's default (hermetic, no DeepSeek)."""
    from msb_v3.wake import runner

    _open_operator(monkeypatch)
    client = TestClient(create_app())

    calls: list[str] = []
    monkeypatch.setattr(
        runner,
        "default_turn_fn",
        lambda: (lambda text, sender: (calls.append(text), f"resident reply to: {text}")[1]),
    )
    # Keep the cycle hermetic: stub the dispatcher + audit legs so the test
    # never touches the real automation stores.
    monkeypatch.setattr(
        runner, "default_dispatcher", lambda: {"ok": True, "summary": "dispatcher: nothing due", "detail": {"ran": [], "failed": []}}
    )
    monkeypatch.setattr(
        runner, "default_audit", lambda: {"ok": True, "summary": "audit: 0 finding(s), unchanged", "findings": [], "changed": False}
    )

    client.post("/wake", json={"text": "what's the disk status?"}, headers=_auth("tok"))
    from msb_v3.cron.actions import run_action

    result = run_action("wake_agent", {})
    assert result["ok"] is True
    assert len(result["detail"]["processed"]) == 1

    r = client.get("/wake/outbox", headers=_auth("tok"))
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert "resident reply to: what's the disk status?" in r.json()["outbox"][0]["text"]
    assert calls == ["what's the disk status?"]
