"""Tests for the hybrid model router (msb_v3.fabric.model_router)."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.agent.intent import Intent  # noqa: E402
from msb_v3.agent.planner import plan  # noqa: E402
from msb_v3.fabric.model_router import (  # noqa: E402
    DEFAULT_TIER,
    FrontierClient,
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


# ---------------------------------------------------------------------------
# FrontierClient — async path (Phase 2 follow-up: /agent/handle must not
# block the server's event loop)
# ---------------------------------------------------------------------------


def _ok_response(text: str = "hi") -> Dict[str, Any]:
    return {
        "choices": [{"message": {"content": text, "tool_calls": []}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }


@pytest.mark.asyncio
async def test_agenerate_returns_local_ai_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer k"
        body = request.read().decode()
        assert "\"model\":\"m\"" in body
        assert "hello" in body
        return httpx.Response(200, json=_ok_response("async-ok"))

    client = FrontierClient(base_url="http://frontier/v1", api_key="k", model="m", transport=httpx.MockTransport(handler))
    resp = await client.agenerate("hello")
    assert resp.text == "async-ok"
    assert resp.model == "m"
    assert resp.prompt_tokens == 5
    assert resp.completion_tokens == 2
    assert resp.latency_s >= 0.0


@pytest.mark.asyncio
async def test_agenerate_raises_connection_error_on_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "backend down"})

    client = FrontierClient(base_url="http://frontier/v1", api_key="k", model="m", transport=httpx.MockTransport(handler))
    with pytest.raises(ConnectionError):
        await client.agenerate("hello")


@pytest.mark.asyncio
async def test_agenerate_concurrent_requests_do_not_serialize() -> None:
    """Concurrency proof: N agenerate calls overlap (all N in flight at once)
    instead of serializing — the async client does not block the loop."""
    in_flight = 0
    max_in_flight = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            await asyncio.sleep(0.1)
            return httpx.Response(200, json=_ok_response("ok"))
        finally:
            in_flight -= 1

    client = FrontierClient(base_url="http://frontier/v1", api_key="k", model="m", transport=httpx.MockTransport(handler))
    results = await asyncio.gather(*[client.agenerate(f"q{i}") for i in range(4)])
    assert [r.text for r in results] == ["ok"] * 4
    assert max_in_flight == 4  # all four were in flight simultaneously


@pytest.mark.asyncio
async def test_plan_with_frontier_client_does_not_block_event_loop() -> None:
    """The /agent/handle concern: plan() awaiting the frontier seam must not
    block the event loop. A heartbeat task keeps ticking while the frontier
    transport sleeps — if plan() blocked the loop, the heartbeat would
    freeze for the whole 0.3s and tick far less."""
    plan_json = (
        '{"tasks": [{"task_id": "research", "goal": "search the vault", "parent_id": null, '
        '"capabilities": ["read_vault"], "tools": ["search_query"], '
        '"expected_output": "sources", "verification_method": "search_returned_hits", '
        '"timeout_s": 90, "retry_policy": "retry:2"}]}'
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)  # slow frontier: a sync call would hold the loop
        return httpx.Response(200, json=_ok_response(plan_json))

    fc = FrontierClient(base_url="http://frontier/v1", api_key="k", model="m", transport=httpx.MockTransport(handler))

    ticks = 0
    stop = False

    async def heartbeat() -> None:
        nonlocal ticks, stop
        while not stop:
            await asyncio.sleep(0.01)
            ticks += 1

    intent = Intent(request="public: plan a search", goals=("search",), source="llm")
    hb = asyncio.ensure_future(heartbeat())
    t0 = time.perf_counter()
    graph = await plan(intent, client=fc)
    elapsed = time.perf_counter() - t0
    stop = True
    await hb

    assert graph.source == "llm"  # the frontier client actually produced the plan
    assert elapsed >= 0.28  # the frontier latency was real (not skipped)
    assert ticks >= 10  # the loop kept running during the frontier await


@pytest.mark.asyncio
async def test_plan_offloads_sync_client_via_thread() -> None:
    """A sync-only client (local Ollama/llama.cpp, fakes) still works through
    plan() — offloaded to a thread so the loop stays free."""

    class _SyncFake:
        def __init__(self, text: str) -> None:
            self._text = text

        def generate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
            time.sleep(0.05)  # pretend to be a slow local model
            return type("R", (), {"text": self._text})()

    graph = await plan(Intent(request="x", goals=("x",)), client=_SyncFake("garbage"))
    assert graph.source == "template"  # graceful fallback on unusable output
