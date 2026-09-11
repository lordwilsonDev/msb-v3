"""Regression: /system/health must not 500 (stale Database import, fixed).

Also covers Phase 2 truth-in-config (FR-2.3 / AC-2.1): the deep check
reports real backend availability — llama.cpp is green only when weights are
provisioned AND the server answers; the frontier row reflects whether
OPENAI_API_KEY is actually set.
"""
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
    from msb_v3.core.config import settings

    monkeypatch.setattr(LocalAIClient, "generate", lambda self, *a, **k: "ok")
    # Pin the active backend so the healthy-status assertion is independent
    # of test order (another test's /models/switch must not flip it).
    monkeypatch.setattr(settings, "_active_backend", "ollama")
    client = TestClient(create_app())
    body = client.get("/system/health").json()
    assert body["ollama"] == "ok"
    assert body["status"] == "healthy"


def test_system_health_llamacpp_reports_real_backend(monkeypatch, tmp_path):
    """AC-2.1: the llama.cpp row must match reality — error when the weights
    file is missing, and ok once the weights exist and the port answers.
    `_probe_llama_cpp` is called with a monkeypatched settings + socket so the
    test never touches the real port."""
    from msb_v3.core.config import settings

    # Deterministic: pin the active-backend path so the test never depends on
    # a live ollama (the ollama row must read "ok" for the status asserts).
    monkeypatch.setattr(LocalAIClient, "generate", lambda self, *a, **k: "ok")
    # llama.cpp is quarantined (off by default) — this test is specifically
    # about the probe's real behavior, so opt in for its whole run.
    monkeypatch.setattr(settings, "llamacpp_enabled", True)

    client = TestClient(create_app())

    # 1. Weights not provisioned -> explicit error naming the missing file.
    monkeypatch.setattr(settings, "llama_cpp_model", str(tmp_path / "missing.gguf"))
    monkeypatch.setattr(settings, "llama_cpp_url", "http://127.0.0.1:8080")
    body = client.get("/system/health").json()
    assert body["llamacpp"].startswith("error: weights not provisioned")

    # 2. Weights exist + HTTP 200 JSON -> ok (real llama-server).
    import httpx

    from msb_v3.api import system

    weights = tmp_path / "gemma.gguf"
    weights.write_bytes(b"fake-gguf")
    monkeypatch.setattr(settings, "llama_cpp_model", str(weights))
    original_probe = system._probe_llama_cpp  # capture before patching

    def ok_json(request):  # noqa: ANN001
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(
        system,
        "_probe_llama_cpp",
        lambda transport=None: original_probe(transport=httpx.MockTransport(ok_json)),
    )
    body = client.get("/system/health").json()
    assert body["llamacpp"] == "ok"

    # 3. The httpd trap: a port that answers HTML (Apache's 404 on :8080)
    #    must NOT count as llama.cpp up — only a JSON /health is real.
    def html_404(request):  # noqa: ANN001
        return httpx.Response(404, text="<!DOCTYPE html><h1>Not Found</h1>", headers={"content-type": "text/html"})

    monkeypatch.setattr(
        system,
        "_probe_llama_cpp",
        lambda transport=None: original_probe(transport=httpx.MockTransport(html_404)),
    )
    body = client.get("/system/health").json()
    assert body["llamacpp"].startswith("error: HTTP 404")

    # 4. Weights exist but nothing listening -> unreachable error.
    def refused(request):  # noqa: ANN001
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(
        system,
        "_probe_llama_cpp",
        lambda transport=None: original_probe(transport=httpx.MockTransport(refused)),
    )
    body = client.get("/system/health").json()
    assert body["llamacpp"].startswith("error: unreachable")

    # 5. Active-backend semantics: ollama active + llamacpp down is NOT
    #    degraded (llama.cpp is optional); the row still reports the error.
    monkeypatch.setattr(settings, "_active_backend", "ollama")
    body = client.get("/system/health").json()
    assert body["active_backend"] == "ollama"
    assert body["llamacpp"].startswith("error:")
    assert body["status"] == "healthy"

    # 6. Switching active backend to llamacpp while it is down -> degraded.
    monkeypatch.setattr(settings, "_active_backend", "llamacpp")
    body = client.get("/system/health").json()
    assert body["active_backend"] == "llamacpp"
    assert body["status"] == "degraded"


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


def test_probe_llamacpp_disabled_by_default_skips_weights_check(monkeypatch, tmp_path):
    """Quarantined (blueprint §3.2): with llamacpp_enabled False and ollama
    active, the probe must not even stat the weights file."""
    from msb_v3.api import system
    from msb_v3.core.config import settings

    monkeypatch.setattr(settings, "llamacpp_enabled", False)
    monkeypatch.setattr(settings, "_active_backend", "ollama")
    monkeypatch.setattr(settings, "llama_cpp_model", str(tmp_path / "never-checked.gguf"))

    assert system._probe_llama_cpp() == "disabled (MSB_LLAMACPP_ENABLED=0)"


def test_probe_llamacpp_auto_enables_when_active_backend(monkeypatch, tmp_path):
    """Switching to llama.cpp as the active backend is, by definition, no
    longer dormant — the probe must run even without the explicit flag."""
    from msb_v3.api import system
    from msb_v3.core.config import settings

    monkeypatch.setattr(settings, "llamacpp_enabled", False)
    monkeypatch.setattr(settings, "_active_backend", "llamacpp")
    monkeypatch.setattr(settings, "llama_cpp_model", str(tmp_path / "missing.gguf"))

    assert system._probe_llama_cpp().startswith("error: weights not provisioned")


def test_probe_paseo_disabled_by_default_skips_daemon_call(monkeypatch):
    """Quarantined (blueprint §3.2): with paseo_enabled False, the probe
    must not attempt the live daemon call, and reports UNKNOWN — never
    FAILED, so a dormant Paseo can't poison overall system health."""
    from msb_v3.api import system
    from msb_v3.core.config import settings

    monkeypatch.setattr(settings, "paseo_enabled", False)
    monkeypatch.setattr(settings, "paseo_url", "http://127.0.0.1:1/unreachable")  # would hard-fail if called

    result = system._probe_paseo()

    assert result == {"status": "UNKNOWN", "detail": "disabled (MSB_PASEO_ENABLED=0)"}


def test_system_health_paseo_disabled_never_fails_overall(monkeypatch):
    """With Paseo quarantined off, an unreachable daemon must not flip
    /system/health's overall status to FAILED. Isolated from every other
    component (vesta's tasks.db, for one, doesn't exist in a fresh/staged
    checkout regardless of Paseo — that's a real but unrelated environment
    fact, not something this test should depend on)."""
    from msb_v3.api import system
    from msb_v3.core.config import settings

    monkeypatch.setattr(LocalAIClient, "generate", lambda self, *a, **k: "ok")
    monkeypatch.setattr(settings, "paseo_enabled", False)
    monkeypatch.setattr(settings, "paseo_url", "http://127.0.0.1:1/unreachable")
    monkeypatch.setattr(system, "_probe_qdrant", lambda: system._component(True, "ok"))
    monkeypatch.setattr(system, "_probe_auditchain", lambda: system._component(True, "ok"))
    monkeypatch.setattr(system, "_probe_vesta", lambda: system._component(True, "ok"))

    client = TestClient(create_app())
    body = client.get("/system/health").json()

    assert body["components"]["paseo"]["status"] == "UNKNOWN"
    assert body["overall"] != "FAILED"
