"""Tenant-scoped RAG API backed by Qdrant with Ollama embeddings."""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

try:
    from qdrant_client import QdrantClient
    _HAS_QDRANT = True
except Exception:  # pragma: no cover
    _HAS_QDRANT = False

_OLLAMA = os.getenv("OLLAMA_HOST", "http://localhost:11434")
_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
_EMBED_DIM = 768


def _qdrant_client() -> Any:
    if not _HAS_QDRANT:
        raise RuntimeError("qdrant client not available")
    host = os.getenv("QDRANT_HOST", "localhost")
    port = int(os.getenv("QDRANT_PORT", "6333"))
    return QdrantClient(host=host, port=port, prefer_grpc=False)


def _collection(tenant_id: str) -> str:
    safe = tenant_id.replace("/", "_").replace(":", "_").replace(" ", "_")
    return f"tenant_{safe}"


_ID_NS = uuid.UUID("9c5c7c3e-6f1a-4b0e-9a2d-3d3e3f4f5f6f")


def _stable_point_id(source: str, chunk: int = 0) -> str:
    """Deterministic point ID for a (source, chunk) pair.

    Qdrant only accepts unsigned integers or UUIDs as point IDs. We derive a
    UUIDv5 from the source path + chunk index, so re-indexing a file replaces
    its own points (idempotent) instead of overwriting other files' points.
    The previous `id: idx` scheme collided across batches (every batch reused
    ids 0..14), silently capping the collection at BATCH_SIZE points no matter
    how many documents were submitted.
    """
    return str(uuid.uuid5(_ID_NS, f"{source}#{chunk}"))


async def _embed(text: str) -> list[float]:
    # nomic-embed-text has a 2048-token context; Ollama rejects longer prompts
    # with HTTP 500 ("input length exceeds the context length"). Truncate and
    # retry so a single long document can never fail the whole batch.
    for attempt in range(4):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{_OLLAMA}/api/embeddings",
                    json={"model": _EMBED_MODEL, "prompt": text},
                )
                resp.raise_for_status()
                data = resp.json()
                vec = data.get("embedding") or []
                if len(vec) != _EMBED_DIM:
                    # Pad or truncate to expected dimension
                    vec = (vec + [0.0] * _EMBED_DIM)[:_EMBED_DIM]
                return vec
        except httpx.HTTPStatusError as exc:
            body = (exc.response.text or "").lower()
            if "context length" in body and len(text) > 500:
                text = text[: len(text) // 2]
                continue
            raise
    raise RuntimeError(f"embedding failed after truncation retries for {len(text)} chars")


class IndexRequest(BaseModel):
    tenant_id: str
    documents: list[dict[str, Any]]


class SearchRequest(BaseModel):
    tenant_id: str
    query: str
    limit: int = 5


@router.post("/index")
async def rag_index(payload: IndexRequest) -> dict[str, Any]:
    if not _HAS_QDRANT:
        raise HTTPException(status_code=501, detail="Qdrant client not installed")

    tenant_id = payload.tenant_id
    collection = _collection(tenant_id)
    client = _qdrant_client()

    try:
        client.create_collection(
            collection_name=collection,
            vectors_config={"size": _EMBED_DIM, "distance": "Cosine"},
        )
    except Exception:
        pass

    points: list[dict[str, Any]] = []
    for idx, doc in enumerate(payload.documents):
        text = str(doc.get("text", ""))
        vector = await _embed(text)
        source = str(doc.get("source", ""))
        chunk = int(doc.get("chunk", 0))
        points.append(
            {
                "id": str(doc.get("id") or _stable_point_id(source, chunk)),
                "vector": vector,
                "payload": {
                    "tenant_id": tenant_id,
                    "text": text,
                    "source": source,
                    "chunk": chunk,
                    "metadata": doc.get("metadata", {}),
                },
            }
        )

    client.upsert(collection_name=collection, points=points)
    return {"ok": True, "tenant_id": tenant_id, "collection": collection, "indexed": len(points)}


@router.post("/search")
async def rag_search(payload: SearchRequest) -> dict[str, Any]:
    if not _HAS_QDRANT:
        raise HTTPException(status_code=501, detail="Qdrant client not installed")

    tenant_id = payload.tenant_id
    collection = _collection(tenant_id)
    client = _qdrant_client()

    try:
        client.get_collection(collection)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Collection not found: {collection}") from exc

    query_vector = await _embed(payload.query)
    results = client.query_points(
        collection_name=collection,
        query=query_vector,
        limit=payload.limit,
        with_payload=True,
    )
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "query": payload.query,
        "results": [
            {
                "score": float(r.score),
                "text": r.payload.get("text", ""),
                "source": r.payload.get("source", ""),
                "metadata": r.payload.get("metadata", {}),
            }
            for r in results.points
        ],
    }
