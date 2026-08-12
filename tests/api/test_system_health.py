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


def test_system_config_exposes_governance_approvals_flywheel(monkeypatch):
    """/system/config exposes the Phase 0B brake settings keyed by env-var
    name, the approval policy constants, and the flywheel loop mechanics —
    all reflecting live settings / true constants."""
    from msb_v3.core.config import settings
    from msb_v3.flywheel.models import (
        APPROVAL_STAGES,
        ITERATIONS_PER_STAGE,
        RESEARCH_STAGES,
        STAGES,
    )
    from msb_v3.governance.approval import APPROVAL_KINDS

    client = TestClient(create_app())
    cfg = client.get("/system/config").json()

    gov = cfg["governance"]
    assert set(gov) == {
        "GOV_BUDGET_RESEARCH_CALLS",
        "GOV_BUDGET_TOKENS",
        "GOV_BUDGET_ITERATIONS",
        "GOV_BUDGET_WINDOW_MIN",
        "GOV_GOVERNOR_STALL_LIMIT",
        "GOV_GOVERNOR_NOVELTY_MIN",
        "GOV_GOVERNOR_DUP_RATIO_HALT",
        "GOV_GOVERNOR_HISTORY",
    }
    assert gov["GOV_BUDGET_RESEARCH_CALLS"] == settings.gov_budget_research_calls
    assert gov["GOV_BUDGET_TOKENS"] == settings.gov_budget_tokens
    assert gov["GOV_BUDGET_ITERATIONS"] == settings.gov_budget_iterations
    assert gov["GOV_GOVERNOR_STALL_LIMIT"] == settings.gov_governor_stall_limit
    assert gov["GOV_GOVERNOR_NOVELTY_MIN"] == settings.gov_governor_novelty_min
    assert gov["GOV_GOVERNOR_DUP_RATIO_HALT"] == settings.gov_governor_dup_ratio_halt
    assert gov["GOV_GOVERNOR_HISTORY"] == settings.gov_governor_history

    assert cfg["approvals"]["kinds_requiring_approval"] == list(APPROVAL_KINDS)
    assert cfg["approvals"]["stages_requiring_approval"] == APPROVAL_STAGES

    assert cfg["flywheel"]["stages"] == list(STAGES)
    assert cfg["flywheel"]["iterations_per_stage"] == ITERATIONS_PER_STAGE
    assert cfg["flywheel"]["research_stages"] == list(RESEARCH_STAGES)

    # live read: a settings change is visible on the next call
    monkeypatch.setattr(settings, "gov_budget_tokens", 12345)
    cfg2 = client.get("/system/config").json()
    assert cfg2["governance"]["GOV_BUDGET_TOKENS"] == 12345
