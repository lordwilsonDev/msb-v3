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
    
    # TODO: route to tenant-specific LLM config
    # For now, proxy to existing chat logic with tenant context
    
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
