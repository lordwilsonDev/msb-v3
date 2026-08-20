"""Tests for the /automation API — operator-gated control surface."""

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


def test_automation_requires_operator_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSB_OPERATOR_TOKEN", raising=False)
    monkeypatch.setattr(settings, "operator_token", "")
    client = TestClient(create_app())
    assert client.post("/automation/create", json={"description": "x"}, headers=_auth("nope")).status_code == 503
    assert client.get("/automation/manifest", headers=_auth("nope")).status_code == 503
    assert client.get("/automation/status", headers=_auth("nope")).status_code == 503


def test_create_dry_run_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /automation/create without approve=true records a dry-run plan —
    no provider contact, nothing created (hermetic: the LLM is stubbed)."""
    from msb_v3.automation import brain

    _open_operator(monkeypatch)

    def fake_llm(messages):
        return '```json\n{"automation": {"provider": "n8n", "name": "echo bot", "description": "echo webhook payloads"}}\n```'

    monkeypatch.setattr(brain, "default_llm", lambda: fake_llm)
    client = TestClient(create_app())

    assert client.post("/automation/create", json={}, headers=_auth("tok")).status_code == 422
    r = client.post("/automation/create", json={"description": "build a webhook echo"}, headers=_auth("tok"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "dry_run"
    assert body["plan"]["provider"] == "n8n"

    r = client.get("/automation/manifest", headers=_auth("tok"))
    assert r.json()["count"] == 1
    assert r.json()["manifest"][0]["status"] == "dry_run"


def test_create_closed_without_brain_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No DEEPSEEK_API_KEY (the brain raises RuntimeError) -> 503, fail-closed
    like the /v1 adapter without a key — never a 500."""
    from msb_v3.automation import brain

    _open_operator(monkeypatch)

    def broken_llm(messages):
        raise RuntimeError("deepseek seam closed: DEEPSEEK_API_KEY not set")

    monkeypatch.setattr(brain, "default_llm", lambda: broken_llm)
    client = TestClient(create_app())
    r = client.post("/automation/create", json={"description": "build a webhook echo"}, headers=_auth("tok"))
    assert r.status_code == 503
    assert "DEEPSEEK_API_KEY" in r.json()["detail"]


def test_status_shows_budget_and_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    _open_operator(monkeypatch)
    client = TestClient(create_app())
    r = client.get("/automation/status", headers=_auth("tok"))
    assert r.status_code == 200
    body = r.json()
    assert body["budget"]["cap_usd"] == 10.0
    assert body["dry_run"] is True
    assert set(body["providers"]) == {"n8n", "make", "zapier", "ghl"}
