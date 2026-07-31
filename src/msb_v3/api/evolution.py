"""Evolution and continuity routers."""
from __future__ import annotations

import datetime
from typing import Any, Dict, List

from fastapi import APIRouter

router = APIRouter(tags=["evolution"])

_EVOLUTION_SCAN: Dict[str, Any] = {
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "hotspots": [],
}
_EVOLUTION_MEMORY: List[Dict[str, Any]] = []
_EVOLUTION_SUMMARY: Dict[str, Any] = {"counts": {}, "by_target": {}, "sample": []}


@router.get("/scan")
async def evolution_scan() -> dict:
    return _EVOLUTION_SCAN


@router.get("/memory/latest")
async def evolution_memory_latest() -> dict:
    latest = _EVOLUTION_MEMORY[-1] if _EVOLUTION_MEMORY else None
    return {"entry": latest}


@router.get("/memory/summary")
async def evolution_memory_summary() -> dict:
    return _EVOLUTION_SUMMARY


@router.post("/memory/record")
async def evolution_memory_record(body: dict) -> dict:
    entry = {
        "target": body.get("target", "unknown"),
        "status": body.get("status", "new"),
        "vdr_improvement": bool(body.get("vdr_improvement", False)),
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "body": {k: v for k, v in body.items() if k not in {"target", "status", "vdr_improvement"}},
    }
    _EVOLUTION_MEMORY.append(entry)
    if len(_EVOLUTION_MEMORY) > 200:
        _EVOLUTION_MEMORY.pop(0)
    _EVOLUTION_SUMMARY["counts"] = dict(sorted({e["status"]: _EVOLUTION_MEMORY.count(e["status"]) for e in _EVOLUTION_MEMORY}.items(), key=lambda x: x[0]))
    _EVOLUTION_SUMMARY["by_target"] = {}
    for e in _EVOLUTION_MEMORY:
        _EVOLUTION_SUMMARY["by_target"].setdefault(e["target"], 0)
        _EVOLUTION_SUMMARY["by_target"][e["target"]] += 1
    _EVOLUTION_SUMMARY["sample"] = [{"target": e["target"], "status": e["status"]} for e in _EVOLUTION_MEMORY[-5:]]
    return entry


@router.post("/memory/batch-update")
async def evolution_memory_batch_update(body: dict) -> dict:
    status = body.get("new_status", body.get("status", "unknown"))
    count = 0
    for entry in _EVOLUTION_MEMORY:
        if entry.get("status") == body.get("status"):
            entry["status"] = status
            count += 1
    return {"updated": count, "new_status": status}


@router.post("/continuity/resume-prompt")
async def continuity_resume_prompt(body: dict | None = None) -> dict:
    return {"prompt": "Resume from latest completion artifact.", "accepted": True}


@router.post("/memory/consolidate")
async def memory_consolidate(body: dict | None = None) -> dict:
    return {"kind": (body or {}).get("kind", "procedural"), "min_items": (body or {}).get("min_items", 1), "status": "ok"}


@router.post("/mesh/discovery/peers")
async def mesh_discovery_peers(query: str | None = None) -> dict:
    return {"peers": [], "count": 0}