"""Index adapters — heterogeneous retrieval over the existing Qdrant store.

Add what you need, use what you have: the msb-v3 RAG index (msb_v3.api.rag)
already provides Qdrant + local Ollama embeddings (nomic-embed-text, 768d).
All three routes are served from that same collection:

  vector     pure cosine similarity over the query embedding
  structural vector search + payload metadata filter (tag:/folder:/author:…)
  temporal   vector search + payload timestamp range (last N days/weeks…)

Adapters normalize every hit to {id, score, text, source, metadata}. They are
lazily imported from msb_v3.api.rag so this package imports cleanly even
where qdrant/ollama are absent (offline unit tests never touch them).

Temporal contract: the temporal route filters on payload
``metadata.timestamp`` as EPOCH SECONDS (float) via a numeric Qdrant Range.
msb-v3's /rag/index passes metadata through untouched, so indexers must store
timestamps in that encoding for the temporal route to match. The opt-in live
test (test_live_smi_query_temporal) seeds conforming data and proves the
filter works end-to-end.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone

_FILTER_PATTERN = re.compile(r"\b(tag|tags|folder|author|category|type)\s*[:=]\s*([a-z0-9_\-/.]+)", re.IGNORECASE)
_WINDOW_PATTERN = re.compile(r"last\s+(\d+)\s+(day|week|month|year)s?", re.IGNORECASE)
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}

# One shared Qdrant client for the whole retrieval package: the per-query
# adapter instances reuse it instead of opening a fresh client per route
# (3 per request otherwise). Lazy init under a lock — safe under the
# engine's concurrent route dispatch.
_shared_client = None
_shared_client_lock = threading.Lock()


def _client():
    global _shared_client
    if _shared_client is None:
        with _shared_client_lock:
            if _shared_client is None:
                from msb_v3.api.rag import _qdrant_client  # lazy: Qdrant optional
                _shared_client = _qdrant_client()
    return _shared_client


def _collection(tenant_id: str) -> str:
    safe = tenant_id.replace("/", "_").replace(":", "_").replace(" ", "_")
    return f"tenant_{safe}"


def _normalize(point) -> dict:
    payload = getattr(point, "payload", {}) or {}
    return {
        "id": str(getattr(point, "id", "")),
        "score": float(getattr(point, "score", 0.0)),
        "text": str(payload.get("text", "")),
        "source": str(payload.get("source", "")),
        "metadata": payload.get("metadata", {}),
    }


def _structural_filters(query: str) -> dict[str, str]:
    """Extract {field: value} metadata constraints from the query (tag:ai …)."""
    return {m.group(1).lower().rstrip("s"): m.group(2) for m in _FILTER_PATTERN.finditer(query)}


def _temporal_cutoff(query: str) -> float:
    """Recency cutoff for the temporal route; default 30 days.

    Returned as epoch seconds: Qdrant Range filters are numeric, and the
    conventional payload encoding for timestamps is Unix time (float).
    """
    m = _WINDOW_PATTERN.search(query.lower())
    if m:
        days = _UNIT_DAYS[m.group(2)] * int(m.group(1))
    elif any(c in query.lower() for c in ("recent", "last week", "this week", "yesterday", "today")):
        days = 7
    else:
        days = 30
    return (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()


class _QdrantBase:
    """Lazy client/embedding access over msb_v3.api.rag.

    Declares the adapter interface (search) so consumers type-check against
    the base class; concrete index routes override it.
    """

    def __init__(self, tenant_id: str = "default"):
        self.tenant_id = tenant_id

    async def search(self, query: str, top_k: int = 5, **_kw) -> list[dict]:
        raise NotImplementedError  # overridden by VectorIndex/StructuralIndex/TemporalIndex

    def _qdrant(self):
        return _client()

    async def _embed(self, text: str) -> list[float]:
        from msb_v3.api.rag import _embed  # lazy: Ollama optional
        return await _embed(text)


class VectorIndex(_QdrantBase):
    name = "vector"

    async def search(self, query: str, top_k: int = 5, **_kw) -> list[dict]:
        vec = await self._embed(query)
        points = self._qdrant().query_points(
            collection_name=_collection(self.tenant_id),
            query=vec, limit=top_k, with_payload=True,
        )
        return [_normalize(p) for p in points.points]


class StructuralIndex(_QdrantBase):
    name = "structural"

    async def search(self, query: str, top_k: int = 5, **_kw) -> list[dict]:
        from qdrant_client.http import models as qm  # lazy import

        filters = _structural_filters(query)
        if not filters:
            return []
        vec = await self._embed(query)
        points = self._qdrant().query_points(
            collection_name=_collection(self.tenant_id),
            query=vec,
            query_filter=qm.Filter(must=[
                qm.FieldCondition(
                    key=f"metadata.{field}",
                    match=qm.MatchValue(value=value),
                )
                for field, value in filters.items()
            ]),
            limit=top_k, with_payload=True,
        )
        return [_normalize(p) for p in points.points]


class TemporalIndex(_QdrantBase):
    """Recency-filtered vector search over metadata.timestamp.

    Contract: payload metadata.timestamp must be EPOCH SECONDS (float) —
    Qdrant Range is numeric. See the module docstring.
    """

    name = "temporal"

    async def search(self, query: str, top_k: int = 5, **_kw) -> list[dict]:
        from qdrant_client.http import models as qm  # lazy import

        cutoff = _temporal_cutoff(query)
        vec = await self._embed(query)
        points = self._qdrant().query_points(
            collection_name=_collection(self.tenant_id),
            query=vec,
            query_filter=qm.Filter(must=[
                qm.FieldCondition(
                    key="metadata.timestamp",
                    range=qm.Range(gte=cutoff),
                ),
            ]),
            limit=top_k, with_payload=True,
        )
        return [_normalize(p) for p in points.points]


ADAPTERS = {cls.name: cls for cls in (VectorIndex, StructuralIndex, TemporalIndex)}


def get_adapter(name: str, tenant_id: str = "default") -> _QdrantBase:
    try:
        return ADAPTERS[name](tenant_id)
    except KeyError as exc:
        raise ValueError(f"unknown index route: {name!r}") from exc
