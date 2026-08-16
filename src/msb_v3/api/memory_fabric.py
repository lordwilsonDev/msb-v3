"""Memory Fabric API — operator-gated durable agent memory (spec §4.2.2).

The fabric persists what the system learned (episodic / semantic /
procedural / architectural), tracks verification states with a full audit
trail, and applies decay so old memory never masquerades as current truth.
All routes are operator-gated — memory carries provenance and can be
sensitive.

Routes:

    POST /memory-fabric/store          persist a memory (returns memory_id)
    GET  /memory-fabric/recall         rank memories for a query
    POST /memory-fabric/verify         transition verification state
    POST /memory-fabric/forget         soft-delete a memory
    POST /memory-fabric/consolidate    merge duplicates + decay everything
    GET  /memory-fabric/{id}           one memory + its verification history
    GET  /memory-fabric/stats          fabric health per tenant
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from msb_v3.api.auth import require_operator
from msb_v3.core.config import settings
from msb_v3.memory_fabric.fabric import MemoryFabric
from msb_v3.memory_fabric.models import MemoryType, VerificationState
from msb_v3.memory_fabric.store import MemoryFabricStore

router = APIRouter()


class StoreRequest(BaseModel):
    content: str
    type: str = "semantic"
    tags: List[str] = Field(default_factory=list)
    importance: float = 0.5
    source_agent: str = ""
    source: str = "api"
    task_id: str = ""
    tenant: str = "default"
    project: str = ""
    tech: str = ""
    decay_factor: float = 0.9


class RecallRequest(BaseModel):
    query: str
    tenant: str = "default"
    project: Optional[str] = None
    tech: Optional[str] = None
    type: Optional[str] = None
    top_k: int = 8
    semantic: bool = True


class VerifyRequest(BaseModel):
    memory_id: str
    to_state: str
    by: str = "operator"
    reason: str = ""


class ForgetRequest(BaseModel):
    memory_id: str
    by: str = "operator"
    reason: str = "forgotten"


class ConsolidateRequest(BaseModel):
    tenant: str = "default"
    by: str = "operator"


def _fabric() -> MemoryFabric:
    return MemoryFabric(MemoryFabricStore(settings.memory_fabric_db_path))


def _type_or_400(raw: Optional[str]) -> Optional[MemoryType]:
    if not raw:
        return None
    try:
        return MemoryType(raw)
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"unknown type: {raw} (episodic|semantic|procedural|architectural)"
        )


@router.post("/store", dependencies=[Depends(require_operator)])
async def store_memory(body: StoreRequest) -> Dict[str, Any]:
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="content is required")
    type_ = _type_or_400(body.type)
    if type_ is None:
        type_ = MemoryType.SEMANTIC
    item = _fabric().store_memory(
        content,
        type_=type_,
        tags=body.tags,
        importance=body.importance,
        source_agent=body.source_agent,
        source=body.source,
        task_id=body.task_id,
        tenant=body.tenant,
        project=body.project,
        tech=body.tech,
        decay_factor=body.decay_factor,
    )
    return {"ok": True, "memory": item.as_dict()}


@router.get("/recall", dependencies=[Depends(require_operator)])
async def recall(
    query: str,
    tenant: str = "default",
    project: Optional[str] = None,
    tech: Optional[str] = None,
    type: Optional[str] = None,
    top_k: int = 8,
    semantic: bool = True,
) -> Dict[str, Any]:
    if not query.strip():
        raise HTTPException(status_code=422, detail="query is required")
    hits = _fabric().recall_memories(
        query,
        tenant=tenant,
        project=project or None,
        tech=tech or None,
        type_=_type_or_400(type),
        top_k=min(max(top_k, 1), 50),
        semantic=semantic,
    )
    return {"ok": True, "count": len(hits), "memories": [h.as_dict() for h in hits]}


@router.post("/verify", dependencies=[Depends(require_operator)])
async def verify(body: VerifyRequest) -> Dict[str, Any]:
    try:
        to_state = VerificationState(body.to_state)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown state: {body.to_state}")
    try:
        item = _fabric().verify_memory(
            body.memory_id, to_state, by=body.by, reason=body.reason
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "memory": item.as_dict()}


@router.post("/forget", dependencies=[Depends(require_operator)])
async def forget(body: ForgetRequest) -> Dict[str, Any]:
    try:
        item = _fabric().forget_memory(body.memory_id, by=body.by, reason=body.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "memory": item.as_dict()}


@router.post("/consolidate", dependencies=[Depends(require_operator)])
async def consolidate(body: ConsolidateRequest) -> Dict[str, Any]:
    return {"ok": True, "consolidation": _fabric().consolidate(body.tenant, by=body.by)}


@router.get("/stats", dependencies=[Depends(require_operator)])
async def stats(tenant: str = "default") -> Dict[str, Any]:
    return {"ok": True, "stats": _fabric().store.stats(tenant)}


@router.get("/{memory_id}", dependencies=[Depends(require_operator)])
async def get_memory(memory_id: str) -> Dict[str, Any]:
    fabric = _fabric()
    item = fabric.store.get(memory_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"unknown memory: {memory_id}")
    return {
        "ok": True,
        "memory": item.as_dict(),
        "verification_history": fabric.store.verification_history(memory_id),
    }
