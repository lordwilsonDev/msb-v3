"""Chat router — single harness entrypoint."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from msb_v3.api.auth import check_auth
from msb_v3.core.container import ApplicationContainer, get_container_dep
from msb_v3.harnesses.base import ChatHarness, HarnessResult
from msb_v3.memory.store import Message

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
async def chat(
    request: Request,
    req: ChatRequest,
    container: ApplicationContainer = Depends(get_container_dep),
) -> ChatResponse:
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
        recent = container.memory_store.recent(session, limit=10)
        hist = "\n".join([f"{m.role}: {m.content}" for m in recent])
        if hist:
            ctx["history"] = hist
        used = len(recent)
    except Exception:
        logger.debug("history fetch failed; treating as zero used", exc_info=True)
        used = 0

    result: HarnessResult = harness.execute(req.query, ctx, session=session)

    # Persist the exchange so the NEXT call's `recent()` read above actually
    # has something to find. Found 2026-09-12: this call read history but
    # never wrote it back, so multi-turn context silently never worked —
    # every /chat call was stateless regardless of `session`. Best-effort:
    # a memory-store hiccup must not fail a chat response that already
    # succeeded.
    try:
        container.memory_store.append(session, Message(role="user", content=req.query))
        container.memory_store.append(session, Message(role="assistant", content=result.payload["text"]))
    except Exception:
        logger.debug("failed to persist chat exchange to memory_store", exc_info=True)

    return ChatResponse(
        ok=result.ok,
        event=result.event,
        payload=ChatPayload(query=result.payload["query"], text=result.payload["text"], model=result.payload["model"]),
        error=result.error,
        history_count=used,
    )
