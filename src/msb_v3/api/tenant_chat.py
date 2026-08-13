"""Tenant-aware chat route with X-Tenant-ID isolation."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class ChatRequest(BaseModel):
    query: str
    messages: list[dict] | None = None
    context: str | None = None


class ChatResponse(BaseModel):
    ok: bool
    event: str
    payload: dict
    error: str | None = None


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    """Tenant-aware chat endpoint.
    
    Requires X-Tenant-ID header for multi-tenant isolation.
    Falls back to 'default' tenant if header missing.
    """
    tenant_id = request.headers.get("X-Tenant-ID", "default")
    
    # In production, validate tenant_id exists
    # For now, accept any tenant_id
    
    messages = body.messages or []
    messages.append({"role": "user", "content": body.query})
    
    # DECISION (2026-08-13): LLM routing is intentionally NOT tenant-scoped
    # yet. Tenant isolation is real on the retrieval/vector side (per-tenant
    # collections via the RAG layer); chat/LLM routing still goes through
    # the global hybrid router (msb_v3.fabric.model_router — R-score +
    # frontier/local /v1 seam). This route is a placeholder and is NOT
    # mounted in api/app.py; wiring it in must come WITH per-tenant model
    # config (settings surface + router integration + tests). Tracked as an
    # open item — do not call multi-tenancy "done" until that lands or this
    # endpoint is removed.
    
    return ChatResponse(
        ok=True,
        event="chat:completed",
        payload={
            "query": body.query,
            "tenant_id": tenant_id,
            "text": f"[tenant:{tenant_id}] Echo: {body.query}",
            "model": "pending",
        },
        error=None,
    )
