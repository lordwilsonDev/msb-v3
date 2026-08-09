"""Memory router — append/recent/clear."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel

from msb_v3.memory.store import MemoryStore, Message

router = APIRouter()
store = MemoryStore()


class MessageIn(BaseModel):
    role: str
    content: str
    tokens: int = 0


class MemoryOut(BaseModel):
    session: str
    messages: List[Dict[str, Any]]


@router.get("/{session}", response_model=MemoryOut)
def get_memory(session: str, limit: int = 50) -> Dict[str, Any]:
    msgs = store.recent(session, limit=limit)
    return {"session": session, "messages": [{"role": m.role, "content": m.content, "ts": m.ts} for m in msgs]}


@router.post("/{session}", response_model=MemoryOut)
def append_memory(session: str, message: MessageIn) -> Dict[str, Any]:
    msg = Message(role=message.role, content=message.content, tokens=message.tokens)
    store.append(session, msg)
    return get_memory(session, limit=50)


@router.delete("/{session}")
def clear_memory(session: str) -> Dict[str, str]:
    store.clear(session)
    return {"status": "cleared"}
