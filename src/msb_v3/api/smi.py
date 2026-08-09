"""SMI-017 semantic routes — /query, /evaluate, /adapt, /report."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["smi"])


class QueryRequest(BaseModel):
    query: str
    context: dict[str, Any] | None = None
    top_k: int = 5


class EvaluateRequest(BaseModel):
    subject: str
    criteria: dict[str, Any] | None = None


class AdaptRequest(BaseModel):
    source: str
    target: str
    payload: dict[str, Any] | None = None


class ReportRequest(BaseModel):
    slug: str
    format: str = "json"


@router.post("/query")
async def semantic_query(body: QueryRequest) -> dict[str, Any]:
    """Semantic Retrieval Router: plan -> parallel dispatch -> RRF fusion ->
    provenance-annotated context (replaces the seed stub).

    Response keeps the historical `matches` + `context` fields and adds the
    retrieval `plan`, per-match `provenance`, `route_errors`, and `latency_ms`.
    """
    from msb_v3.retrieval.engine import (
        RetrievalRouter,  # lazy: no heavy imports at app build
    )

    tenant_id = (body.context or {}).get("tenant_id", "default")
    return await RetrievalRouter(tenant_id=tenant_id).run(body.query, top_k=body.top_k)


@router.post("/evaluate")
async def semantic_evaluate(body: EvaluateRequest) -> dict[str, Any]:
    score = 0.75
    if body.criteria:
        score = min(0.99, score + sum(body.criteria.values()) / max(len(body.criteria), 1) * 0.2)
    return {
        "subject": body.subject,
        "score": round(score, 3),
        "criteria": body.criteria or {},
    }


@router.post("/adapt")
async def semantic_adapt(body: AdaptRequest) -> dict[str, Any]:
    return {
        "source": body.source,
        "target": body.target,
        "mapping": {body.source: body.target},
        "payload": body.payload or {},
    }


@router.post("/report")
async def semantic_report(body: ReportRequest) -> dict[str, Any]:
    return {
        "slug": body.slug,
        "format": body.format,
        "status": "generated",
        "path": f"runtime/research/{body.slug}/report.{body.format}",
    }
