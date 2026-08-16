"""Phase 1.2 — the unified VectorStore abstraction and its two backends.

Proves the completion-blueprint success criterion: application code talks to
``VectorStore``, and switching Qdrant <-> SQLite changes only the backend
selection — the same documents + query rank identically through both.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from msb_v3.retrieval.vector_store import (
    QdrantVectorStore,
    SQLiteVectorStore,
    VectorDocument,
    VectorStore,
    VectorStoreUnavailable,
    get_vector_store,
)


async def _fake_embed(text: str) -> list[float]:
    """Deterministic, offline embedder: 8-dim unit-ish vector from a hash.

    Distinct texts get distinct (but stable) vectors; repeated calls are
    idempotent, so parity comparisons across backends are meaningful.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [((b / 255.0) * 2.0 - 1.0) for b in digest[:8]]


def _docs() -> list[VectorDocument]:
    return [
        VectorDocument(id="a", text="alpha the first", source="s1"),
        VectorDocument(id="b", text="beta the second", source="s2"),
        VectorDocument(id="c", text="alpha again", source="s1", chunk=1),
    ]


def _top_ids(hits) -> list[str]:
    return [h.id for h in hits]


class _FakeQdrant:
    """Minimal in-memory Qdrant stand-in: stores points, answers query_points
    with in-process cosine, so the Qdrant backend is exercised end-to-end
    without a server."""

    def __init__(self) -> None:
        self._points: dict[str, dict[str, Any]] = {}
        self.collections: list[str] = []

    def _cosine(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    def create_collection(self, collection_name: str, vectors_config: dict | None = None) -> None:
        if collection_name not in self.collections:
            self.collections.append(collection_name)

    def upsert(self, collection_name: str, points: list[dict]) -> None:
        for p in points:
            self._points[p["id"]] = {"vector": p["vector"], "payload": p["payload"]}

    def query_points(self, collection_name: str, query: list[float], limit: int, **kw):
        scored = []
        for pid, p in self._points.items():
            scored.append((self._cosine(query, p["vector"]), pid, p["payload"]))
        scored.sort(key=lambda t: t[0], reverse=True)
        return _FakePoints([_FakePoint(pid, score, payload) for score, pid, payload in scored[:limit]])

    def delete(self, collection_name: str, points_selector: list[str]):
        for pid in points_selector:
            self._points.pop(pid, None)
        return type("_R", (), {})()

    def get_collections(self):
        return type("_C", (), {"collections": self.collections})()

    def get_collection(self, collection_name: str):
        return type("_I", (), {"points_count": len(self._points)})()


class _FakePoint:
    def __init__(self, id: str, score: float, payload: dict) -> None:
        self.id = id
        self.score = score
        self.payload = payload


class _FakePoints:
    def __init__(self, points: list[_FakePoint]) -> None:
        self.points = points


# --- interface conformance -------------------------------------------------


@pytest.mark.parametrize("backend", ["qdrant", "sqlite"])
def test_factory_returns_vectorstore(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    store = get_vector_store(backend)
    assert isinstance(store, VectorStore)


def test_factory_env_backend(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("MSB_VECTOR_BACKEND", "sqlite")
    monkeypatch.setenv("MSB_VECTOR_SQLITE_PATH", str(tmp_path / "v.db"))
    assert isinstance(get_vector_store(), SQLiteVectorStore)


def test_factory_unknown_backend() -> None:
    with pytest.raises(ValueError):
        get_vector_store("not-a-backend")


# --- sqlite backend (offline) ---------------------------------------------


@pytest.mark.asyncio
async def test_sqlite_roundtrip_search_delete_snapshot(tmp_path) -> None:
    store = SQLiteVectorStore(db_path=tmp_path / "v.db", tenant_id="t", embedder=_fake_embed)
    assert (await store.health())["ok"] is True

    indexed = await store.index(_docs())
    assert indexed == 3

    hits = await store.search("alpha", limit=2)
    # both alpha-ish docs outrank the beta doc
    assert {h.id for h in hits} == {"a", "c"}
    assert hits[0].score > 0.0

    assert await store.delete(["a"]) == 1
    assert store.snapshot()["points"] == 2
    assert await store.update([VectorDocument(id="b", text="beta updated")]) == 1


@pytest.mark.asyncio
async def test_sqlite_tenant_isolation(tmp_path) -> None:
    store = SQLiteVectorStore(db_path=tmp_path / "v.db", tenant_id="t1", embedder=_fake_embed)
    await store.index(_docs())
    other = SQLiteVectorStore(db_path=tmp_path / "v.db", tenant_id="t2", embedder=_fake_embed)
    assert await other.search("alpha", limit=5) == []


# --- qdrant backend --------------------------------------------------------


@pytest.mark.asyncio
async def test_qdrant_index_and_search_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeQdrant()
    monkeypatch.setattr("msb_v3.api.rag._qdrant_client", lambda: fake)
    store = QdrantVectorStore(tenant_id="t", embedder=_fake_embed)

    assert await store.index(_docs()) == 3
    assert len(fake._points) == 3

    hits = await store.search("alpha", limit=2)
    assert {h.id for h in hits} == {"a", "c"}
    # payload round-trips id/text/source/metadata
    assert hits[0].text
    assert hits[0].source in {"s1", "s2"}


@pytest.mark.asyncio
async def test_qdrant_unavailable_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom():
        raise RuntimeError("qdrant down")

    monkeypatch.setattr("msb_v3.api.rag._qdrant_client", _boom)
    store = QdrantVectorStore(tenant_id="t", embedder=_fake_embed)
    health = await store.health()
    assert health["ok"] is False
    with pytest.raises(VectorStoreUnavailable):
        await store.search("alpha")


# --- the Phase 1.2 success criterion: backend switch parity ----------------


@pytest.mark.asyncio
async def test_backend_switch_parity(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Same documents + query through both backends rank identically.

    The application changes only `get_vector_store(backend)`, never its own
    logic — this is the blueprint's "switch without changing application code"
    acceptance test.
    """
    qdrant_fake = _FakeQdrant()
    monkeypatch.setattr("msb_v3.api.rag._qdrant_client", lambda: qdrant_fake)

    sqlite = get_vector_store("sqlite", tenant_id="t", db_path=tmp_path / "v.db", embedder=_fake_embed)
    qdrant = get_vector_store("qdrant", tenant_id="t", embedder=_fake_embed)

    for store in (sqlite, qdrant):
        await store.index(_docs())

    query = "alpha"
    sqlite_rank = _top_ids(await sqlite.search(query, limit=3))
    qdrant_rank = _top_ids(await qdrant.search(query, limit=3))

    assert sqlite_rank == qdrant_rank
    assert "a" in sqlite_rank and "c" in sqlite_rank
