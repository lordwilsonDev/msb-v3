"""Offline tests for the Semantic Retrieval Router (P0).

Proves the deterministic planner, weighted RRF fusion, parallel dispatch,
provenance assembly, graceful route failure, and the /smi/query endpoint —
with fake adapters and canned records. No Qdrant, no Ollama, no LLM, no
network (zero-spend by construction).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app
from msb_v3.retrieval import engine as engine_mod
from msb_v3.retrieval import fusion, planner
from msb_v3.retrieval.indexes import _collection, _structural_filters, _temporal_cutoff

# ---------------------------------------------------------------------------
# Planner — deterministic, zero-LLM
# ---------------------------------------------------------------------------

def test_plan_vector_only():
    plan = planner.plan_query("how does the renewal process work", top_k=5)
    routes = plan["routes"]
    assert [r["index"] for r in routes] == ["vector"]
    assert routes[0]["weight"] == 1.0
    assert routes[0]["top_k"] == 10
    assert plan["provenance"] is True


def test_plan_temporal_cue():
    plan = planner.plan_query("meetings from last week")
    routes = plan["routes"]
    assert "temporal" in [r["index"] for r in routes]
    assert routes[0]["index"] == "vector"  # vector route always first
    assert sum(r["weight"] for r in routes) == pytest.approx(1.0)


def test_plan_structural_cue():
    plan = planner.plan_query("notes tagged:ai about agents")
    assert "structural" in [r["index"] for r in plan["routes"]]


def test_plan_both_cues_weights_sum_to_one():
    plan = planner.plan_query("notes tagged:ai from last week")
    routes = plan["routes"]
    assert {r["index"] for r in routes} == {"vector", "temporal", "structural"}
    assert sum(r["weight"] for r in routes) == pytest.approx(1.0)


def test_plan_deterministic():
    a = planner.plan_query("what changed last month in the vault")
    b = planner.plan_query("what changed last month in the vault")
    assert a == b


# ---------------------------------------------------------------------------
# Fusion — weighted RRF
# ---------------------------------------------------------------------------

def _rec(doc_id: str, score: float) -> dict:
    return {"id": doc_id, "score": score, "text": f"text-{doc_id}",
            "source": f"src-{doc_id}", "metadata": {}}


def test_rrf_shared_doc_rises():
    lists = {"vector": [_rec("a", 0.9), _rec("b", 0.8)],
             "temporal": [_rec("b", 0.7), _rec("c", 0.6)]}
    out = fusion.rrf(lists, {"vector": 0.6, "temporal": 0.4})
    assert out[0]["id"] == "b"  # present in both lists -> highest fused score
    routes = {r["route"] for r in out[0]["routes"]}
    assert {"vector", "temporal"} <= routes
    assert out[1]["id"] in ("a", "c")


def test_rrf_weight_boosts_route():
    lists = {"vector": [_rec("a", 1.0)], "temporal": [_rec("c", 1.0)]}
    heavy = fusion.rrf(lists, {"vector": 0.9, "temporal": 0.1})
    light = fusion.rrf(lists, {"vector": 0.1, "temporal": 0.9})
    assert heavy[0]["id"] == "a"
    assert light[0]["id"] == "c"


def test_rrf_deterministic():
    lists = {"vector": [_rec("x", 0.9)], "temporal": [_rec("x", 0.8)]}
    out = fusion.rrf(lists, {"vector": 0.5, "temporal": 0.5})
    assert out[0]["id"] == "x"
    assert fusion.rrf(lists, {"vector": 0.5, "temporal": 0.5}) == out


# ---------------------------------------------------------------------------
# Index helpers — pure functions, offline (no Qdrant needed)
# ---------------------------------------------------------------------------

def test_collection_name_sanitized():
    assert _collection("default") == "tenant_default"
    assert _collection("a/b:c d") == "tenant_a_b_c_d"


def test_structural_filters_extracts_metadata():
    out = _structural_filters("notes tags:ai folder:vault about agents")
    assert out == {"tag": "ai", "folder": "vault"}
    # "tagged" is not a tag: filter — the regex requires the colon/equals
    assert _structural_filters("notes tagged:ai") == {}
    assert _structural_filters("plain query") == {}


def test_temporal_cutoff_is_epoch_float():
    import time as _time

    recent = _temporal_cutoff("meetings last 7 days")
    default = _temporal_cutoff("plain query")
    assert isinstance(recent, float)
    # ~7 days ago, within a minute of tolerance
    assert abs(recent - (_time.time() - 7 * 86400)) < 60
    assert abs(default - (_time.time() - 30 * 86400)) < 60


# ---------------------------------------------------------------------------
# Engine — parallel dispatch with fake adapters
# ---------------------------------------------------------------------------

class _FakeAdapter:
    def __init__(self, results: list[dict]):
        self.results = results
        self.calls: list[int] = []

    async def search(self, query: str, top_k: int = 5, **_kw) -> list[dict]:
        self.calls.append(top_k)
        return list(self.results)[: top_k]


def _patch_adapters(monkeypatch, factory):
    """factory(name, tenant_id) -> adapter-or-exception; mirrors the real
    get_adapter(name, tenant_id='default') contract; replaces it in engine."""
    monkeypatch.setattr(engine_mod, "get_adapter", factory)


async def _run(query: str, factory, top_k: int = 5) -> dict:
    from msb_v3.retrieval.engine import RetrievalRouter
    return await RetrievalRouter(tenant_id="t1").run(query, top_k=top_k)


def test_engine_dispatches_only_planned_routes(monkeypatch):
    made: dict[str, _FakeAdapter] = {}

    def factory(name, tenant_id="default"):
        made[name] = _FakeAdapter([{"id": "d1", "score": 0.9, "text": "t", "source": "s1", "metadata": {}}])
        return made[name]

    _patch_adapters(monkeypatch, factory)
    out = asyncio.run(_run("what is the renewal process", factory))
    assert set(made) == {"vector"}  # no cues -> vector route only
    assert out["plan"]["routes"][0]["index"] == "vector"
    assert out["matches"][0]["source"] == "s1"
    assert out["route_errors"] == {}


def test_engine_multi_route_provenance(monkeypatch):
    made: dict[str, _FakeAdapter] = {}

    def factory(name, tenant_id="default"):
        results = [
            {"id": f"{name}-1", "score": 0.9, "text": f"text-{name}", "source": f"src-{name}", "metadata": {}},
        ]
        if name == "vector":  # shared doc appears in vector + temporal
            results.append({"id": "shared", "score": 0.95, "text": "text-shared", "source": "src-shared", "metadata": {}})
        elif name == "temporal":
            results.insert(0, {"id": "shared", "score": 0.8, "text": "text-shared", "source": "src-shared", "metadata": {}})
        made[name] = _FakeAdapter(results)
        return made[name]

    _patch_adapters(monkeypatch, factory)
    out = asyncio.run(_run("notes tagged:ai from last week", factory))
    assert set(made) == {"vector", "temporal", "structural"}
    shared = next(m for m in out["matches"] if m["source"] == "src-shared")
    routes = {p["route"] for p in shared["provenance"]}
    assert {"vector", "temporal"} <= routes  # provenance records both contributors


def test_engine_route_error_degrades(monkeypatch):
    made: dict[str, _FakeAdapter] = {}

    def factory(name, tenant_id="default"):
        if name == "temporal":
            raise RuntimeError("qdrant down")
        made[name] = _FakeAdapter([{"id": f"{name}-1", "score": 0.9, "text": "t", "source": f"src-{name}", "metadata": {}}])
        return made[name]

    _patch_adapters(monkeypatch, factory)
    out = asyncio.run(_run("what changed last week", factory))  # vector + temporal
    assert "temporal" in out["route_errors"]
    assert out["matches"]  # vector route still delivered
    assert all("temporal" not in {p["route"] for p in m["provenance"]} for m in out["matches"])


def test_engine_empty_results(monkeypatch):
    def factory(name, tenant_id="default"):
        return _FakeAdapter([])

    _patch_adapters(monkeypatch, factory)
    out = asyncio.run(_run("nothing here", factory))
    assert out["matches"] == []
    assert out["route_errors"] == {}


# ---------------------------------------------------------------------------
# Endpoint — /smi/query wired to the engine (engine faked; no network)
# ---------------------------------------------------------------------------

def test_smi_query_endpoint(monkeypatch):
    class _FakeRouter:
        def __init__(self, tenant_id: str = "default"):
            self.tenant_id = tenant_id

        async def run(self, query: str, top_k: int = 5) -> dict:
            return {
                "query": query,
                "plan": {"routes": [{"index": "vector", "weight": 1.0, "top_k": 10}],
                          "rerank": False, "verify": False, "provenance": True},
                "matches": [{"score": 0.9, "source": "s1", "text": "hello",
                              "metadata": {}, "provenance": [{"route": "vector", "rank": 1, "score": 0.01}]}],
                "context": {"tenant_id": self.tenant_id},
                "route_errors": {},
                "latency_ms": 3,
            }

    monkeypatch.setattr("msb_v3.retrieval.engine.RetrievalRouter", _FakeRouter)
    client = TestClient(create_app())
    resp = client.post("/smi/query", json={"query": "hello world", "top_k": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert data["plan"]["routes"][0]["index"] == "vector"
    assert data["matches"][0]["source"] == "s1"
    assert data["matches"][0]["provenance"][0]["route"] == "vector"
