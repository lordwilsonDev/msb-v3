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
    return {
        "query": body.query,
        "matches": [
            {"score": 0.92, "source": f"{body.query}-seed-1"},
            {"score": 0.87, "source": f"{body.query}-seed-2"},
        ][: body.top_k],
        "context": body.context or {},
    }


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
