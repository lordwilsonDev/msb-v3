"""Chat router — single harness entrypoint."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request
from pydantic import BaseModel

from msb_v3.harnesses.base import ChatHarness, HarnessResult

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    query: str
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

    result: HarnessResult = harness.execute(req.query, ctx)
    return {"ok": result.ok, "event": result.event, "payload": result.payload, "error": result.error}
