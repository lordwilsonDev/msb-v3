"""MCP search tests — semantic-first search_query, substring fallback,
retired search_simple, and the hippocampus counter wiring in rag.py.

The chaos audit found search_query was literal substring matching, so
multi-word queries like "sovereign core architecture" silently returned
[] while the words sat in the vault. This suite pins the fix: semantic
via /rag/search first, substring only as a loud fallback (mode field),
never a silent empty result.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from msb_v3.api import mcp_bridge
from msb_v3.api.app import create_app

SECRET = "secret-token"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    monkeypatch.setattr(mcp_bridge, "_MCP_BRIDGE_SECRET", SECRET, raising=False)


def _post(client: TestClient, payload: dict[str, object]):
    return client.post("/mcp/proxy", json=payload, headers={"x-mcp-secret": SECRET})


class _FakeResp:
    def __init__(self, payload: dict | None = None, error: Exception | None = None) -> None:
        self._payload = payload or {}
        self._error = error

    def raise_for_status(self) -> None:
        if self._error:
            raise self._error

    def json(self) -> dict:
        return self._payload


class _FakeHTTPClient:
    def __init__(self, handler) -> None:
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def post(self, path: str, json: dict | None = None, timeout=None) -> _FakeResp:
        return self._handler(path, json)


def _fake_async_client(handler):
    """Replacement for httpx.AsyncClient: the bridge calls it as
    `AsyncClient(base_url=..., timeout=...)` then enters the result as an
    async context manager, so this must be a *function* returning the fake
    client. `handler(path, payload)` returns the fake response (or raises,
    to exercise the fallback path)."""

    def factory(*a, **k):
        return _FakeHTTPClient(handler)

    return factory


def test_search_query_uses_semantic_engine(client, monkeypatch) -> None:
    """search_query proxies /rag/search (semantic) and returns scored
    snippets — multi-word queries no longer depend on exact phrases."""
    hits = [
        {"score": 0.92, "text": "The sovereign core architecture is the live brain.", "source": "30_Architecture/core.md", "metadata": {}},
        {"score": 0.85, "text": "Sovereign principles in practice.", "source": "40_Memory/notes.md", "metadata": {}},
    ]
    calls: list[tuple[str, dict]] = []

    def handler(path, payload):
        calls.append((path, payload))
        return _FakeResp({"ok": True, "results": hits})

    monkeypatch.setattr(mcp_bridge.httpx, "AsyncClient", _fake_async_client(handler))

    resp = _post(client, {"tool": "search_query", "args": {"query": "sovereign core architecture"}})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["mode"] == "semantic"
    matches = result["matches"]
    assert len(matches) == 2
    assert matches[0]["path"] == "30_Architecture/core.md"
    assert matches[0]["score"] == 0.92
    assert "live brain" in matches[0]["snippet"]
    # the right engine was asked, with the right tenant
    assert calls and calls[0][0] == "/rag/search"
    assert calls[0][1]["tenant_id"] == "wilson-vault"
    assert calls[0][1]["query"] == "sovereign core architecture"


def test_search_query_falls_back_to_substring_when_semantic_down(client, tmp_path, monkeypatch) -> None:
    """If /rag/search is down (or empty), the tool falls back to the literal
    substring scan — loudly (mode: substring), never silently empty."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "core.md").write_text("Our sovereign core architecture sits right here. Sovereign principles guide it.")

    def handler(path, payload):
        # a real network failure is an httpx.RequestError — exactly what the
        # bridge's narrowed except is designed to catch and degrade on
        raise httpx.RequestError("qdrant down")

    monkeypatch.setattr(mcp_bridge, "_VAULT_BASE", vault.resolve(), raising=False)
    monkeypatch.setattr(mcp_bridge.httpx, "AsyncClient", _fake_async_client(handler))

    resp = _post(client, {"tool": "search_query", "args": {"query": "sovereign core architecture"}})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["mode"] == "substring"
    matches = result["matches"]
    assert len(matches) == 1
    assert matches[0]["path"] == "core.md"
    assert matches[0]["score"] is None


def test_search_query_empty_is_no_firehose(client) -> None:
    """An empty query returns no matches — the old tools matched every file."""
    resp = _post(client, {"tool": "search_query", "args": {"query": ""}})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["matches"] == []
    assert result["mode"] == "empty"


def test_search_query_null_is_treated_as_empty(client) -> None:
    """A JSON null query behaves like an empty one — it must not search for
    the literal string 'None' (the chaos audit fuzzed null payloads)."""
    resp = _post(client, {"tool": "search_query", "args": {"query": None}})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["matches"] == []
    assert result["mode"] == "empty"


def test_search_simple_retired_with_clear_signal(client) -> None:
    """search_simple is retired: the manifest no longer advertises it, and a
    call gets an explicit retired note instead of a silent 404."""
    resp = _post(client, {"tool": "search_simple", "args": {"query": "x"}})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["retired"] is True
    assert "search_query" in result["note"]

    names = [t["name"] for t in mcp_bridge._MCP_TOOLS]
    assert "search_simple" not in names
    assert "search_query" in names


def test_rag_search_increments_hippocampus_counter(client, monkeypatch) -> None:
    """The hippocampus vector-search counter moves exactly when /rag/search
    actually runs — the chaos audit saw hippocampus_total=0 because the live
    search path never used the vector engine. Pinned here so it cannot regress."""
    from msb_v3.api import rag as rag_api
    from msb_v3.observability.metrics import TRIUMVIRATE_HIPPOCAMPUS

    label = TRIUMVIRATE_HIPPOCAMPUS.labels(op="search")
    before = label._value.get()

    class _FakeQdrant:
        def get_collection(self, name):  # noqa: ARG002
            return None

        def query_points(self, **kw):  # noqa: ARG002
            class _Points:
                points = []

            return _Points()

    async def _fake_embed(t: str):  # noqa: ANN001
        return [0.0] * 768

    monkeypatch.setattr(rag_api, "_HAS_QDRANT", True)
    monkeypatch.setattr(rag_api, "_qdrant_client", lambda: _FakeQdrant())
    monkeypatch.setattr(rag_api, "_embed", _fake_embed)
    try:
        r = client.post("/rag/search", json={"tenant_id": "t", "query": "x", "limit": 5})
        assert r.status_code == 200
        assert label._value.get() == before + 1
    finally:
        label._value.set(before)  # restore — the counter is global state
