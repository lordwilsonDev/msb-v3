"""Tenant-scoped RAG API backed by Qdrant."""

from __future__ import annotations

import hashlib
import os
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

EMBED_MODEL = os.getenv("MSB_EMBED_MODEL", "nomic-embed-text")
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_DIM = 768


def _qdrant_client() -> Any:
    if not _HAS_QDRANT:
        raise RuntimeError("qdrant client not available")
    host = os.getenv("QDRANT_HOST", "localhost")
    port = int(os.getenv("QDRANT_PORT", "6333"))
    return QdrantClient(host=host, port=port, prefer_grpc=False)


async def _embed(text: str) -> list[float]:
    """Real embedding via Ollama. Raises if the embed call fails -- callers
    must not silently fall back to a placeholder vector, since a fake
    vector is worse than an explicit error (it looks like it worked)."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{OLLAMA_BASE}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
        )
        resp.raise_for_status()
        data = resp.json()
        vec = data["embeddings"][0]
        if len(vec) != EMBED_DIM:
            raise ValueError(f"embedding dim {len(vec)} != expected {EMBED_DIM}")
        return vec


def _stable_id(tenant_id: str, source: str, idx: int) -> int:
    """Deterministic id from tenant+source+idx so re-indexing the same
    source updates the existing point instead of colliding with whatever
    happened to be at id=0,1,2... from a previous unrelated /index call."""
    h = hashlib.sha1(f"{tenant_id}:{source}:{idx}".encode()).hexdigest()
    return int(h[:16], 16) % (2**63)


def _collection(tenant_id: str) -> str:
    safe = tenant_id.replace("/", "_").replace(":", "_").replace(" ", "_")
    return f"tenant_{safe}"


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
            vectors_config={"size": 768, "distance": "Cosine"},
        )
    except Exception:
        pass

    points: list[dict[str, Any]] = []
    for idx, doc in enumerate(payload.documents):
        text = str(doc.get("text", ""))
        source = str(doc.get("source", ""))
        vector = await _embed(text)
        points.append(
            {
                "id": _stable_id(tenant_id, source, idx),
                "vector": vector,
                "payload": {
                    "tenant_id": tenant_id,
                    "text": text,
                    "source": source,
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
