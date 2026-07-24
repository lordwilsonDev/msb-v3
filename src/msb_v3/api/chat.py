"""Chat router — single harness entrypoint."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request
from pydantic import BaseModel

from msb_v3.harnesses.base import ChatHarness, HarnessResult
from msb_v3.memory.store import MemoryStore

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    query: str
    session: str = "default"
    system: str | None = None
    tools: list[Dict[str, Any]] | None = None


@router.post("/chat")
async def chat(request: Request, req: ChatRequest) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {}
    if req.system:
        ctx["system"] = req.system
    if req.tools:
        ctx["tools"] = req.tools

    app = request.app
    harness: ChatHarness | None = getattr(app.state, "chat", None)
    if harness is None:
        harness = ChatHarness()
        app.state.chat = harness

    try:
        store = MemoryStore()
        recent = store.recent(req.session, limit=10)
        hist = "\n".join([f"{m.role}: {m.content}" for m in recent])
        if hist:
            ctx["history"] = hist
    except Exception:
        pass

    result: HarnessResult = harness.execute(req.query, ctx, session=req.session)
    return {"ok": result.ok, "event": result.event, "payload": result.payload, "error": result.error}
