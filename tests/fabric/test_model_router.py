"""Tests for the hybrid model router (msb_v3.fabric.model_router)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.fabric.model_router import (  # noqa: E402
    DEFAULT_TIER,
    ModelRouter,
    resolve_client,
)


@pytest.fixture(autouse=True)
def _closed_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to the real environment: /v1 seam closed (no key)
    unless a test explicitly opens it — decisions then degrade honestly."""
    monkeypatch.setattr("msb_v3.core.config.settings.openai_api_key", "")


def test_plan_defaults_to_frontier_when_seam_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("msb_v3.core.config.settings.openai_api_key", "sk-test")
    decision = ModelRouter().decide("plan")
    assert decision.tier == "frontier"
    assert decision.available is True
    assert decision.task_kind == "plan"
    assert decision.model == "frontier"


def test_plan_degrades_to_local_when_seam_closed() -> None:
    decision = ModelRouter().decide("plan")
    assert decision.tier == "local"  # degraded, never faked
    assert decision.available is False
    assert "degraded to local" in decision.reason


def test_routine_kinds_are_local_even_with_seam_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("msb_v3.core.config.settings.openai_api_key", "sk-test")
    router = ModelRouter()
    for kind in ("classify", "embed", "route", "routine_tool_call", "chat"):
        decision = router.decide(kind)
        assert decision.tier == "local", kind
        assert DEFAULT_TIER[kind] == "local"


def test_privacy_scoped_plan_is_forced_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("msb_v3.core.config.settings.openai_api_key", "sk-test")
    decision = ModelRouter().decide("plan", privacy_scoped=True)
    assert decision.tier == "local"
    assert "privacy-scoped" in decision.reason
    assert decision.privacy_scoped is True


def test_hard_capability_overrides_routine_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("msb_v3.core.config.settings.openai_api_key", "sk-test")
    decision = ModelRouter().decide("chat", hard_capability=True)
    assert decision.tier == "frontier"
    assert "capability" in decision.reason


def test_decide_is_deterministic() -> None:
    router = ModelRouter()
    a = router.decide("plan", privacy_scoped=True)
    b = router.decide("plan", privacy_scoped=True)
    assert a == b
    assert a.as_dict() == b.as_dict()


def test_score_is_bounded_and_components_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("msb_v3.core.config.settings.openai_api_key", "sk-test")
    decision = ModelRouter().decide("plan")
    assert 0.0 <= decision.score <= 1.0
    assert set(decision.components) == {"privacy", "capability", "latency", "cost", "hardware", "confidence"}


def test_router_metric_moves(monkeypatch: pytest.MonkeyPatch) -> None:
    from prometheus_client.registry import REGISTRY

    monkeypatch.setattr("msb_v3.core.config.settings.openai_api_key", "sk-test")

    def count(kind: str, tier: str, cause: str) -> float:
        return (
            REGISTRY.get_sample_value(
                "msb_v3_router_decisions_total",
                {"task_kind": kind, "tier": tier, "cause": cause},
            )
            or 0.0
        )

    before = count("plan", "frontier", "tier-default")
    ModelRouter().decide("plan")
    ModelRouter().decide("plan")
    assert count("plan", "frontier", "tier-default") == before + 2


def test_available_override_wins_over_config() -> None:
    # available=False pinned even though the key is unset: degradation path.
    router = ModelRouter(available=False)
    decision = router.decide("plan")
    assert decision.tier == "local"
    assert decision.available is False


def test_resolve_client_injected_client_wins() -> None:
    fake = object()
    client, decision = resolve_client("plan", client=fake)
    assert client is fake
    assert decision is not None  # decision still computed + logged


def test_resolve_client_closed_seam_returns_local_client() -> None:
    client, decision = resolve_client("plan")
    assert decision is not None
    assert decision.tier == "local"
    # The local backend may be ollama or llamacpp depending on the active
    # backend — the point is it is NOT a FrontierClient.
    assert client.__class__.__name__ in ("LocalAIClient", "LlamaCPPClient")


def test_resolve_client_open_seam_returns_frontier_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("msb_v3.core.config.settings.openai_api_key", "sk-test")
    client, decision = resolve_client("plan")
    assert decision is not None
    assert decision.tier == "frontier"
    assert client.__class__.__name__ == "FrontierClient"
