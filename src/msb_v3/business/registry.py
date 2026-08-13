"""Registry of Truth — sovereign knowledge verification layer."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()

# Max serialized payload size for truth entities, in bytes. Enforced on write
# so a single bad client or bug cannot grow the truth registry without bound.
# (hygiene h10: oversized payloads were accepted with 200 — now 413.)
MAX_PAYLOAD_BYTES = int(os.getenv("MSB_MAX_PAYLOAD_BYTES", "262144"))  # 256 KiB


def _truth_dir() -> Path:
    return Path(os.getenv("MSB_TRUTH_DIR", "data/truth")).resolve()


def _entity_path(entity_id: str) -> Path:
    base = _truth_dir()
    path = (base / f"{entity_id}.json").resolve()
    if path.parent != base:
        raise HTTPException(status_code=400, detail="invalid entity id")
    return path


def _checksum(content: dict) -> str:
    raw = json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


@router.post("/register")
async def register_truth(payload: dict[str, Any]) -> dict[str, Any]:
    """Register a sovereign truth entity."""
    size = len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Payload too large: {size} bytes exceeds limit "
                f"{MAX_PAYLOAD_BYTES} bytes"
            ),
        )
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
        except Exception as exc:
            logger.debug("skipping unreadable entity file %s: %s", p, exc)
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
