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


def test_system_config_exposes_rate_limit_guards(monkeypatch):
    """/system/config exposes the live /v1 guard settings keyed by their
    env-var names, and reflects a live change without a restart."""
    from msb_v3.core.config import settings

    client = TestClient(create_app())
    rl = client.get("/system/config").json()["rate_limits"]
    assert set(rl) == {
        "OPENAI_CHAT_RATE_MAX",
        "OPENAI_CHAT_RATE_WINDOW_S",
        "OPENAI_EMBED_MAX_BATCH",
        "OPENAI_EMBED_RATE_MAX",
        "OPENAI_EMBED_RATE_WINDOW_S",
    }
    assert rl["OPENAI_CHAT_RATE_MAX"] == settings.openai_chat_rate_max
    assert rl["OPENAI_CHAT_RATE_WINDOW_S"] == settings.openai_chat_rate_window_s
    assert rl["OPENAI_EMBED_MAX_BATCH"] == settings.openai_embed_max_batch
    assert rl["OPENAI_EMBED_RATE_MAX"] == settings.openai_embed_rate_max
    assert rl["OPENAI_EMBED_RATE_WINDOW_S"] == settings.openai_embed_rate_window_s

    # live read: a settings change (the test equivalent of editing .env +
    # reload) is visible on the next call
    monkeypatch.setattr(settings, "openai_chat_rate_max", 7)
    assert client.get("/system/config").json()["rate_limits"]["OPENAI_CHAT_RATE_MAX"] == 7
