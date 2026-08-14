"""Chat router — single harness entrypoint."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from msb_v3.api.auth import check_auth
from msb_v3.harnesses.base import ChatHarness, HarnessResult
from msb_v3.memory.store import MemoryStore

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"], dependencies=[Depends(check_auth)])


class ToolSpec(BaseModel):
    type: str = "function"
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class ChatRequest(BaseModel):
    query: str
    session: str = "default"
    system: Optional[str] = None
    tools: Optional[List[ToolSpec]] = None


class ChatPayload(BaseModel):
    query: str
    text: str
    model: str


class ChatResponse(BaseModel):
    ok: bool
    event: str
    payload: ChatPayload
    error: Optional[str] = None
    history_count: int = 0


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, req: ChatRequest) -> ChatResponse:
    tenant_id = request.headers.get("X-Tenant-ID", "default")
    session = f"{tenant_id}:{req.session}" if tenant_id != "default" else req.session
    ctx: Dict[str, Any] = {}
    if req.system:
        ctx["system"] = req.system
    if req.tools:
        ctx["tools"] = [t.model_dump() for t in req.tools]
    vesta_bind = getattr(request.state, "vesta_bind", None)
    if vesta_bind is not None:
        # Propagate the immutable trust context without treating it as model
        # authority or adding it to the user-visible prompt.
        ctx["vesta_bind"] = vesta_bind

    from msb_v3.harnesses.base import ChatHarness

    app = request.app
    harness: ChatHarness | None = getattr(app.state, "chat", None)
    if harness is None:
        harness = ChatHarness()
        app.state.chat = harness

    try:
        store = MemoryStore()
        recent = store.recent(session, limit=10)
        hist = "\n".join([f"{m.role}: {m.content}" for m in recent])
        if hist:
            ctx["history"] = hist
        used = len(recent)
    except Exception:
        logger.debug("history fetch failed; treating as zero used", exc_info=True)
        used = 0

    result: HarnessResult = harness.execute(req.query, ctx, session=session)
    return ChatResponse(
        ok=result.ok,
        event=result.event,
        payload=ChatPayload(query=result.payload["query"], text=result.payload["text"], model=result.payload["model"]),
        error=result.error,
        history_count=used,
    )
