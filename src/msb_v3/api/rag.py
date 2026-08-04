"""Tenant-scoped RAG API backed by Qdrant."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

try:
    from qdrant_client import QdrantClient
    _HAS_QDRANT = True
except Exception:  # pragma: no cover
    _HAS_QDRANT = False


def _qdrant_client() -> Any:
    if not _HAS_QDRANT:
        raise RuntimeError("qdrant client not available")
    host = os.getenv("QDRANT_HOST", "localhost")
    port = int(os.getenv("QDRANT_PORT", "6333"))
    return QdrantClient(host=host, port=port, prefer_grpc=False)


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
        points.append(
            {
                "id": idx,
                "vector": [0.0] * 768,
                "payload": {
                    "tenant_id": tenant_id,
                    "text": text,
                    "source": doc.get("source", ""),
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
        collection_info = client.get_collection(collection)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Collection not found: {collection}") from exc

    results = client.query_points(
        collection_name=collection,
        query=[0.0] * 768,
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
