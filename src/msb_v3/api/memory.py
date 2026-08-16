"""Memory router — append/recent/clear."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from msb_v3.api.auth import check_auth
from msb_v3.core.container import ApplicationContainer, get_container_dep
from msb_v3.memory.store import MemoryStore, Message

router = APIRouter(dependencies=[Depends(check_auth)])


class MessageIn(BaseModel):
    role: str
    content: str
    tokens: int = 0


class MemoryOut(BaseModel):
    session: str
    messages: List[Dict[str, Any]]


def _memory_out(store: MemoryStore, session: str, limit: int) -> Dict[str, Any]:
    msgs = store.recent(session, limit=limit)
    return {"session": session, "messages": [{"role": m.role, "content": m.content, "ts": m.ts} for m in msgs]}


@router.get("/{session}", response_model=MemoryOut)
def get_memory(
    session: str,
    limit: int = 50,
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    return _memory_out(container.memory_store, session, limit)


@router.post("/{session}", response_model=MemoryOut)
def append_memory(
    session: str,
    message: MessageIn,
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    msg = Message(role=message.role, content=message.content, tokens=message.tokens)
    container.memory_store.append(session, msg)
    return _memory_out(container.memory_store, session, limit=50)


@router.delete("/{session}")
def clear_memory(
    session: str,
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, str]:
    container.memory_store.clear(session)
    return {"status": "cleared"}
