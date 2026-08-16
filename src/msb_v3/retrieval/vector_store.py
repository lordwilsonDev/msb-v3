"""Unified vector-store abstraction (completion blueprint — Phase 1.2).

The repo historically had two independent vector stores with no shared
contract: the Qdrant-backed RAG store (``msb_v3.api.rag`` + the
``retrieval/indexes.py`` adapters) and ``triumvirate.hardware_sovereignty.
VectorHippocampus`` (SQLite + in-process cosine). This module defines ONE
``VectorStore`` interface with two interchangeable backends so application
code depends on the interface, not on Qdrant or SQLite directly:

  QdrantVectorStore  — tenant-scoped, real ANN via the existing rag.py client
  SQLiteVectorStore  — tenant-scoped, offline cosine (no Qdrant/Ollama needed)

Switch backends by calling ``get_vector_store("qdrant")`` vs
``get_vector_store("sqlite")`` (or ``MSB_VECTOR_BACKEND``) — no application
change. The parity test in ``tests/retrieval/test_vector_store.py`` proves the
two backends rank the same documents for the same query.

Embeddings: every backend accepts an injectable async ``embedder``
(``text -> list[float]``). Documents may carry a precomputed ``embedding``
(embedding-passing callers), in which case the embedder is never invoked;
otherwise the backend embeds ``text`` lazily via the default Ollama embedder
(``msb_v3.api.rag._embed``) so this module imports cleanly where
qdrant/ollama are absent.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

logger = logging.getLogger(__name__)

Embedder = Callable[[str], Awaitable[list[float]]]

_ID_NS = uuid.UUID("9c5c7c3e-6f1a-4b0e-9a2d-3d3e3f4f5f6f")


class VectorStoreUnavailable(RuntimeError):
    """The selected backend is unreachable/unusable — callers may fall back.

    Raised instead of letting a raw qdrant/ollama/sqlite exception leak, so a
    caller can degrade gracefully (switch backend or report a structured
    failure) without depending on the backend's own exception types.
    """


@dataclass
class VectorDocument:
    """One unit of indexable content.

    ``id`` is the logical identifier; it is stored verbatim in the payload
    and returned on ``VectorHit`` (backend-internal point ids are derived).
    """

    id: str
    text: str
    source: str = ""
    chunk: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None


@dataclass
class VectorHit:
    """A normalized search hit — identical shape across backends."""

    id: str
    score: float
    text: str
    source: str
    metadata: dict[str, Any]


def _default_embedder() -> Embedder:
    """The real Ollama embedder, imported lazily so offline code never
    touches rag.py (which pulls in qdrant_client) at import time."""
    from msb_v3.api.rag import _embed

    return _embed


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


class VectorStore:
    """Backend-agnostic contract (Phase 1.2).

    All methods are async because the default embedder is an HTTP call;
    callers that pass precomputed embeddings still pay only the local cost.
    """

    async def index(self, documents: Iterable[VectorDocument]) -> int:
        raise NotImplementedError

    async def search(
        self,
        query: str = "",
        limit: int = 5,
        query_embedding: list[float] | None = None,
    ) -> list[VectorHit]:
        raise NotImplementedError

    async def delete(self, ids: list[str]) -> int:
        raise NotImplementedError

    async def update(self, documents: Iterable[VectorDocument]) -> int:
        raise NotImplementedError

    async def health(self) -> dict:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError


class _EmbeddingMixin:
    """Shared embed-when-missing logic for both backends."""

    def __init__(self, embedder: Embedder | None = None) -> None:
        self._embedder = embedder

    def _embedder_or_default(self) -> Embedder:
        return self._embedder or _default_embedder()

    async def _embed(self, text: str) -> list[float]:
        return await self._embedder_or_default()(text)

    async def _embed_missing(self, documents: Iterable[VectorDocument]) -> list[VectorDocument]:
        out: list[VectorDocument] = []
        for doc in documents:
            if doc.embedding is None:
                doc = replace(doc, embedding=await self._embed(doc.text))
            out.append(doc)
        return out


class QdrantVectorStore(_EmbeddingMixin, VectorStore):
    """Tenant-scoped ANN store over the existing rag.py Qdrant client.

    The collection name and client are resolved lazily from ``msb_v3.api.rag``
    so a process without qdrant_client installed still imports this module;
    operations raise ``VectorStoreUnavailable`` when the backend can't be
    reached.
    """

    def __init__(
        self,
        tenant_id: str = "default",
        embedder: Embedder | None = None,
    ) -> None:
        _EmbeddingMixin.__init__(self, embedder)
        self.tenant_id = tenant_id

    def _qdrant(self):
        from msb_v3.api.rag import _qdrant_client

        try:
            return _qdrant_client()
        except Exception as exc:  # qdrant_client missing / unconstructible
            raise VectorStoreUnavailable(f"qdrant backend unavailable: {exc}") from exc

    def _collection(self) -> str:
        from msb_v3.api.rag import _collection

        return _collection(self.tenant_id)

    @staticmethod
    def _point_id(doc: VectorDocument) -> str:
        # Deterministic, idempotent UUIDv5: re-indexing the same (id, chunk)
        # replaces its own point instead of appending duplicates.
        return str(uuid.uuid5(_ID_NS, f"{doc.id}#{doc.chunk}"))

    def _ensure_collection(self, client, collection: str) -> None:
        try:
            client.create_collection(
                collection_name=collection,
                vectors_config={"size": 768, "distance": "Cosine"},
            )
        except Exception:
            # Already-exists and reachability errors both surface here; an
            # unreachable server fails again on the subsequent upsert.
            pass

    async def index(self, documents: Iterable[VectorDocument]) -> int:
        docs = await self._embed_missing(documents)
        client = self._qdrant()
        collection = self._collection()
        self._ensure_collection(client, collection)
        points = [
            {
                "id": self._point_id(d),
                "vector": d.embedding,
                "payload": {
                    "id": d.id,
                    "text": d.text,
                    "source": d.source,
                    "chunk": d.chunk,
                    "metadata": d.metadata,
                },
            }
            for d in docs
        ]
        try:
            client.upsert(collection_name=collection, points=points)
        except Exception as exc:
            raise VectorStoreUnavailable(f"qdrant upsert failed: {exc}") from exc
        return len(points)

    async def search(
        self,
        query: str = "",
        limit: int = 5,
        query_embedding: list[float] | None = None,
    ) -> list[VectorHit]:
        vec = query_embedding if query_embedding is not None else await self._embed(query)
        client = self._qdrant()
        collection = self._collection()
        try:
            result = client.query_points(
                collection_name=collection,
                query=vec,
                limit=limit,
                with_payload=True,
            )
        except Exception as exc:
            raise VectorStoreUnavailable(f"qdrant search failed: {exc}") from exc
        hits = []
        for point in result.points:
            payload = getattr(point, "payload", {}) or {}
            hits.append(
                VectorHit(
                    id=str(payload.get("id", point.id)),
                    score=float(getattr(point, "score", 0.0)),
                    text=str(payload.get("text", "")),
                    source=str(payload.get("source", "")),
                    metadata=payload.get("metadata", {}) or {},
                )
            )
        return hits

    async def delete(self, ids: list[str]) -> int:
        if not ids:
            return 0
        client = self._qdrant()
        collection = self._collection()
        try:
            client.delete(
                collection_name=collection,
                points_selector=[self._point_id(VectorDocument(id=i, text="")) for i in ids],
            )
        except Exception as exc:
            raise VectorStoreUnavailable(f"qdrant delete failed: {exc}") from exc
        return len(ids)

    async def update(self, documents: Iterable[VectorDocument]) -> int:
        # Qdrant upsert is idempotent on point id, so update == index.
        return await self.index(documents)

    async def health(self) -> dict:
        try:
            client = self._qdrant()
            collections = client.get_collections()
            return {"backend": "qdrant", "ok": True, "collections": len(collections.collections)}
        except Exception as exc:
            return {"backend": "qdrant", "ok": False, "error": str(exc)}

    def snapshot(self) -> dict:
        try:
            client = self._qdrant()
            info = client.get_collection(self._collection())
            return {
                "backend": "qdrant",
                "tenant_id": self.tenant_id,
                "collection": self._collection(),
                "points": getattr(info, "points_count", None),
            }
        except Exception as exc:
            return {"backend": "qdrant", "tenant_id": self.tenant_id, "error": str(exc)}


class SQLiteVectorStore(_EmbeddingMixin, VectorStore):
    """Tenant-scoped offline vector store (SQLite + in-process cosine).

    No Qdrant and no Ollama required when documents/queries carry precomputed
    embeddings; the embedder is only touched for text that needs embedding.
    """

    def __init__(
        self,
        db_path: str | os.PathLike[str] | None = None,
        tenant_id: str = "default",
        embedder: Embedder | None = None,
    ) -> None:
        _EmbeddingMixin.__init__(self, embedder)
        self.tenant_id = tenant_id
        self.db_path = Path(db_path) if db_path is not None else self._default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @staticmethod
    def _default_db_path() -> Path:
        # Lazy: config import is deferred so this module imports cleanly in
        # offline unit tests (which always pass an explicit db_path).
        from msb_v3.core.config import settings

        return Path(settings.db_path).parent / "vectors" / "vectors.db"

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vectors (
                    id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    chunk INTEGER NOT NULL DEFAULT 0,
                    embedding BLOB NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    ts REAL NOT NULL,
                    PRIMARY KEY (tenant_id, id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_vectors_tenant_ts ON vectors(tenant_id, ts)"
            )

    @staticmethod
    def _serialize_embedding(embedding: list[float]) -> bytes:
        return json.dumps(embedding).encode()

    @staticmethod
    def _deserialize_embedding(data: bytes) -> list[float]:
        try:
            return json.loads(data)
        except Exception:
            logger.debug("embedding payload unreadable; treating as empty", exc_info=True)
            return []

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    async def index(self, documents: Iterable[VectorDocument]) -> int:
        docs = await self._embed_missing(documents)
        with self._conn() as conn:
            for d in docs:
                conn.execute(
                    "REPLACE INTO vectors(id, tenant_id, text, source, chunk, embedding, metadata, ts) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        d.id,
                        self.tenant_id,
                        d.text,
                        d.source,
                        d.chunk,
                        self._serialize_embedding(d.embedding or []),
                        json.dumps(d.metadata, ensure_ascii=False),
                        _now_ts(),
                    ),
                )
        return len(docs)

    async def search(
        self,
        query: str = "",
        limit: int = 5,
        query_embedding: list[float] | None = None,
    ) -> list[VectorHit]:
        vec = query_embedding if query_embedding is not None else await self._embed(query)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, text, source, chunk, embedding, metadata FROM vectors "
                "WHERE tenant_id = ? ORDER BY ts DESC LIMIT ?",
                (self.tenant_id, max(limit * 10, 10)),
            ).fetchall()
        scored = []
        for row in rows:
            scored.append(
                VectorHit(
                    id=row["id"],
                    score=self._cosine(vec, self._deserialize_embedding(row["embedding"])),
                    text=row["text"],
                    source=row["source"],
                    metadata=json.loads(row["metadata"] or "{}"),
                )
            )
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:limit]

    async def delete(self, ids: list[str]) -> int:
        if not ids:
            return 0
        with self._conn() as conn:
            cur = conn.execute(
                f"DELETE FROM vectors WHERE tenant_id = ? AND id IN ({','.join('?' * len(ids))})",
                (self.tenant_id, *ids),
            )
            return cur.rowcount

    async def update(self, documents: Iterable[VectorDocument]) -> int:
        return await self.index(documents)

    async def health(self) -> dict:
        try:
            with self._conn() as conn:
                conn.execute("SELECT 1").fetchone()
            return {"backend": "sqlite", "ok": True}
        except Exception as exc:
            return {"backend": "sqlite", "ok": False, "error": str(exc)}

    def snapshot(self) -> dict:
        try:
            with self._conn() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM vectors WHERE tenant_id = ?", (self.tenant_id,)
                ).fetchone()[0]
            return {"backend": "sqlite", "tenant_id": self.tenant_id, "points": count}
        except Exception as exc:
            return {"backend": "sqlite", "tenant_id": self.tenant_id, "error": str(exc)}


def get_vector_store(
    backend: str | None = None,
    tenant_id: str = "default",
    **kwargs: Any,
) -> VectorStore:
    """Resolve the configured backend.

    ``backend`` (or ``MSB_VECTOR_BACKEND``, default ``qdrant``) selects the
    implementation; any extra kwargs are forwarded to the constructor
    (``db_path``/``embedder`` for sqlite, ``embedder`` for qdrant).
    """
    name: str = backend or os.getenv("MSB_VECTOR_BACKEND") or "qdrant"
    if name == "qdrant":
        return QdrantVectorStore(tenant_id=tenant_id, **kwargs)
    if name == "sqlite":
        return SQLiteVectorStore(tenant_id=tenant_id, **kwargs)
    raise ValueError(f"unknown vector backend {name!r} (expected 'qdrant' or 'sqlite')")
