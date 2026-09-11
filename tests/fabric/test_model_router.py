"""Tests for the local-only model router (msb_v3.fabric.model_router).

The frontier seam was retired 2026-09-09 (D1): the router routes every task
to the local backend and never selects a remote tier. These tests pin the
local-only contract — deterministic decisions, honest reasons, the decision
record, the Prometheus counter, and the local resolve_client path.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.agent.intent import Intent  # noqa: E402
from msb_v3.agent.planner import plan  # noqa: E402
from msb_v3.fabric.model_router import (  # noqa: E402
    DEFAULT_TIER,
    ModelRouter,
    resolve_client,
)


def test_all_task_kinds_route_local() -> None:
    router = ModelRouter()
    for kind in ("plan", "verify_synth", "classify", "embed", "route", "routine_tool_call", "chat"):
        decision = router.decide(kind)
        assert decision.tier == "local", kind
        assert decision.available is True
        assert DEFAULT_TIER[kind] == "local"


def test_decision_carries_local_model_and_honest_reason() -> None:
    from msb_v3.core.config import settings

    decision = ModelRouter().decide("plan")
    assert decision.model == settings.ollama_model
    assert "local" in decision.reason


def test_privacy_scoped_plan_is_local_with_reason() -> None:
    decision = ModelRouter().decide("plan", privacy_scoped=True)
    assert decision.tier == "local"
    assert decision.privacy_scoped is True
    assert "privacy-scoped" in decision.reason


def test_decide_is_deterministic() -> None:
    router = ModelRouter()
    a = router.decide("plan", privacy_scoped=True)
    b = router.decide("plan", privacy_scoped=True)
    assert a == b
    assert a.as_dict() == b.as_dict()


def test_router_metric_moves() -> None:
    from prometheus_client.registry import REGISTRY

    def count(kind: str, tier: str, cause: str) -> float:
        return (
            REGISTRY.get_sample_value(
                "msb_v3_router_decisions_total",
                {"task_kind": kind, "tier": tier, "cause": cause},
            )
            or 0.0
        )

    before = count("plan", "local", "local-only")
    ModelRouter().decide("plan")
    ModelRouter().decide("plan")
    assert count("plan", "local", "local-only") == before + 2


def test_available_override_still_honoured() -> None:
    # available=False pins degraded-execution paths for callers/tests.
    router = ModelRouter(available=False)
    decision = router.decide("plan")
    assert decision.tier == "local"
    assert decision.available is False


def test_resolve_client_injected_client_wins() -> None:
    fake = object()
    client, decision = resolve_client("plan", client=fake)
    assert client is fake
    assert decision is not None  # decision still computed + logged


def test_resolve_client_returns_local_client() -> None:
    client, decision = resolve_client("plan")
    assert decision is not None
    assert decision.tier == "local"
    # The local backend may be ollama or llamacpp depending on the active
    # backend — the point is it is a local client, never a remote seam.
    assert client.__class__.__name__ in ("LocalAIClient", "LlamaCPPClient")


# ---------------------------------------------------------------------------
# plan() — the async path must not block the event loop, and a failing local
# client must degrade to the deterministic template DAG (never raise, never
# fake a plan).
# ---------------------------------------------------------------------------


class _SlowAsyncClient:
    """Async-capable fake: agenerate exists, so plan() awaits it directly."""

    def __init__(self, text: str, delay_s: float = 0.0, fail: bool = False) -> None:
        self._text = text
        self._delay_s = delay_s
        self._fail = fail

    async def agenerate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
        if self._fail:
            raise ConnectionError("local backend down")
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        return type("R", (), {"text": self._text})()


@pytest.mark.asyncio
async def test_plan_with_async_client_does_not_block_event_loop() -> None:
    """plan() awaiting an async client must not block the loop — a heartbeat
    task keeps ticking while the client sleeps."""
    plan_json = (
        '{"tasks": [{"task_id": "research", "goal": "search the vault", "parent_id": null, '
        '"capabilities": ["read_vault"], "tools": ["search_query"], '
        '"expected_output": "sources", "verification_method": "search_returned_hits", '
        '"timeout_s": 90, "retry_policy": "retry:2"}]}'
    )
    client = _SlowAsyncClient(plan_json, delay_s=0.3)

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
    graph = await plan(intent, client=client)
    elapsed = time.perf_counter() - t0
    stop = True
    await hb

    assert graph.source == "llm"  # the client actually produced the plan
    assert elapsed >= 0.28  # the client latency was real (not skipped)
    assert ticks >= 10  # the loop kept running during the await


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


@pytest.mark.asyncio
async def test_plan_degrades_to_template_when_local_client_fails() -> None:
    """Kill the local backend: a failing client must degrade plan() to the
    deterministic template DAG — never raise, never fake a plan."""
    intent = Intent(request="public: plan a search", goals=("search",), source="llm")
    graph = await plan(intent, client=_SlowAsyncClient("", fail=True))
    assert graph.source == "template"  # safe degrade, never faked
    assert [t.task_id for t in graph.tasks] == ["research", "synthesize"]
