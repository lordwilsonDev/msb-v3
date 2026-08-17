"""Tests for retrieval domains (msb_v3.fabric.retrieval_router)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.fabric.retrieval_router import (  # noqa: E402
    FabricRetrievalRouter,
    detect_domain,
)


def test_detect_domain_defaults_to_knowledge() -> None:
    assert detect_domain("What does the vault say about caching") == "knowledge"
    assert detect_domain("") == "knowledge"


def test_detect_domain_episodic_on_recency_cues() -> None:
    assert detect_domain("what happened last week in the project") == "episodic"
    assert detect_domain("recent events") == "episodic"
    assert detect_domain("show me the newest decisions") == "episodic"


def test_detect_domain_declared_wins() -> None:
    # Even a recency-cued query stays knowledge when declared so.
    assert detect_domain("what happened last week", declared="knowledge") == "knowledge"
    assert detect_domain("anything at all", declared="episodic") == "episodic"


def test_detect_domain_unknown_declared_falls_back() -> None:
    assert detect_domain("recent stuff", declared="bogus-domain") == "episodic"
    assert detect_domain("plain query", declared=None) == "knowledge"


class _FakeEngine:
    """Mirrors RetrievalRouter.run's contract without touching Qdrant."""

    def __init__(self, matches: list[dict], errors: dict[str, str] | None = None) -> None:
        self._matches = matches
        self._errors = errors or {}
        self.calls: list[dict] = []

    async def run(self, query: str, top_k: int = 5, routes: list[str] | None = None) -> dict:
        self.calls.append({"query": query, "top_k": top_k, "routes": routes})
        return {
            "matches": self._matches,
            "route_errors": self._errors,
            "latency_ms": 3,
        }


def _router_with(engine: _FakeEngine) -> FabricRetrievalRouter:
    router = FabricRetrievalRouter("test-tenant")
    router._engine = engine  # inject the fake (hermetic, no Qdrant)
    return router


@pytest.mark.asyncio
async def test_run_knowledge_uses_vector_and_structural_routes() -> None:
    engine = _FakeEngine([{"id": "a", "score": 0.9, "text": "hit", "source": "s.md"}])
    result = await _router_with(engine).run("multi word query here", top_k=5)
    assert result.domain == "knowledge"
    assert engine.calls[0]["routes"] == ["vector", "structural"]
    assert result.matches[0]["id"] == "a"


@pytest.mark.asyncio
async def test_run_episodic_with_hits_stays_episodic() -> None:
    engine = _FakeEngine([{"id": "e", "score": 0.8, "text": "event", "source": "runs.jsonl"}])
    result = await _router_with(engine).run("what happened last week", top_k=3)
    assert result.domain == "episodic"
    assert engine.calls[0]["routes"] == ["temporal"]
    assert result.route_errors == {}
    assert result.fallback_from is None


@pytest.mark.asyncio
async def test_run_empty_episodic_degrades_to_semantic() -> None:
    """A recency-cued query whose temporal route finds nothing must not
    silently return empty — degrade to the vector route and say so. This is
    the live failure the core-loop case-safe run exposed (2026-08-17):
    "recent decisions about the sovereign stack" was hijacked into episodic
    and returned zero hits even though the semantic store had the content."""
    engine = _FakeEngine([])  # temporal finds nothing
    result = await _router_with(engine).run("recent decisions about the sovereign stack", top_k=5)
    assert result.domain == "semantic"
    assert result.fallback_from == "episodic"
    assert engine.calls[0]["routes"] == ["temporal"]
    assert engine.calls[1]["routes"] == ["vector"]
    assert result.route_errors == {}


@pytest.mark.asyncio
async def test_run_semantic_uses_vector_route() -> None:
    engine = _FakeEngine([])
    result = await _router_with(engine).run("caching tradeoffs", domain="semantic", top_k=2)
    assert result.domain == "semantic"
    assert engine.calls[0]["routes"] == ["vector"]
    assert engine.calls[0]["top_k"] == 2


@pytest.mark.asyncio
async def test_run_surfaces_route_errors_without_crashing() -> None:
    """A route that errored (qdrant down) is surfaced honestly and is NOT
    retried — an empty result with a visible error beats a pointless second
    call that will fail the same way."""
    engine = _FakeEngine([], errors={"temporal": "qdrant down"})
    result = await _router_with(engine).run("recent decisions")
    assert result.domain == "episodic"
    assert result.route_errors["temporal"] == "qdrant down"
    assert result.latency_ms == 3
    assert result.fallback_from is None
    assert len(engine.calls) == 1  # no degrade on an errored route


@pytest.mark.asyncio
async def test_run_single_token_query_uses_knowledge_route() -> None:
    """A single-token query ('sovereign') must route exactly like a phrase —
    knowledge domain, vector + structural routes — and must not be treated
    as a cue-less degenerate case. (Retrieval validation: token queries.)"""
    engine = _FakeEngine([{"id": "t", "score": 0.68, "text": "hit", "source": "s.md"}])
    result = await _router_with(engine).run("sovereign", top_k=5)
    assert result.domain == "knowledge"
    assert result.fallback_from is None
    assert engine.calls[0]["routes"] == ["vector", "structural"]
    assert result.matches[0]["id"] == "t"
    assert len(engine.calls) == 1  # no pointless degrade


@pytest.mark.asyncio
async def test_run_irrelevant_query_is_honest_not_firehose() -> None:
    """An irrelevant/nonsense query returns exactly what the vector engine
    found (its low-confidence top hits, or empty) with the route visible —
    never a fabricated fallback claim and never a silent firehose. The router
    must not pretend an irrelevant query is a routed-domain miss."""
    engine = _FakeEngine([])  # engine says: nothing relevant
    result = await _router_with(engine).run("zzqxv kjhqwop nvmcx", top_k=5)
    assert result.domain == "knowledge"
    assert result.matches == []
    assert result.route_errors == {}
    assert result.fallback_from is None  # empty-with-no-error is honest, no lie
    assert len(engine.calls) == 1  # a nonsense query is not a cue miss; one honest answer


@pytest.mark.asyncio
async def test_run_empty_query_routes_knowledge_without_crash() -> None:
    """An empty query must not crash or firehose: it routes to knowledge,
    calls the engine once with the empty string, and returns the engine's
    (empty) answer. (Retrieval validation: empty queries.)"""
    engine = _FakeEngine([])
    result = await _router_with(engine).run("", top_k=5)
    assert result.domain == "knowledge"
    assert result.matches == []
    assert result.route_errors == {}
    assert result.fallback_from is None
    assert len(engine.calls) == 1
    assert engine.calls[0]["query"] == ""
