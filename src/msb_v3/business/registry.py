"""Registry of Truth — sovereign knowledge verification layer."""

from __future__ import annotations

import os
import json
import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


def _truth_dir() -> Path:
    return Path(os.getenv("MSB_TRUTH_DIR", "data/truth"))


def _entity_path(entity_id: str) -> Path:
    return _truth_dir() / f"{entity_id}.json"


def _checksum(content: dict) -> str:
    raw = json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


@router.post("/register")
async def register_truth(payload: dict[str, Any]) -> dict[str, Any]:
    """Register a sovereign truth entity."""
    entity_id = payload.get("id") or hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()[:16]
    payload["id"] = entity_id
    payload["checksum"] = _checksum(payload)
    _truth_dir().mkdir(parents=True, exist_ok=True)
    path = _entity_path(entity_id)
    if path.exists():
        raise HTTPException(status_code=409, detail=f"Entity {entity_id} already exists")
    path.write_text(json.dumps(payload, indent=2))
    return {"ok": True, "id": entity_id, "path": str(path)}


@router.get("/retrieve/{entity_id}")
async def retrieve_truth(entity_id: str) -> dict[str, Any]:
    """Retrieve a registered truth entity by ID."""
    path = _entity_path(entity_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")
    data = json.loads(path.read_text())
    expected = data.get("checksum", "")
    actual = _checksum({k: v for k, v in data.items() if k != "checksum"})
    if expected != actual:
        raise HTTPException(status_code=409, detail=f"Checksum mismatch for {entity_id}")
    return {"ok": True, "data": data}


@router.get("/list")
async def list_truth() -> dict[str, Any]:
    """List all registered truth entities."""
    entities = []
    for p in sorted(_truth_dir().glob("*.json")):
        try:
            data = json.loads(p.read_text())
            entities.append({"id": data.get("id", p.stem), "path": str(p)})
        except Exception:
            continue
    return {"ok": True, "entities": entities, "count": len(entities)}


@router.delete("/purge/{entity_id}")
async def purge_truth(entity_id: str) -> dict[str, Any]:
    """Purge a truth entity (irreversible)."""
    path = _entity_path(entity_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")
    path.unlink()
    return {"ok": True, "purged": entity_id}
