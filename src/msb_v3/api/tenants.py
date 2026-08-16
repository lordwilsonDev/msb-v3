"""Tenant isolation — sovereign multi-tenant routing layer."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from msb_v3.api.auth import require_operator

logger = logging.getLogger(__name__)
router = APIRouter()


def _tenant_dir() -> Path:
    return Path(os.getenv("MSB_TENANT_DIR", "data/tenants")).resolve()


def _tenant_path(tenant_id: str) -> Path:
    base = _tenant_dir()
    path = (base / f"{tenant_id}.json").resolve()
    if path.parent != base:
        raise HTTPException(status_code=400, detail="invalid tenant id")
    return path


@router.get("")
async def list_tenants() -> dict[str, Any]:
    """List all registered tenants."""
    tenants = []
    for p in sorted(_tenant_dir().glob("*.json")):
        try:
            data = json.loads(p.read_text())
            tenants.append({"id": data.get("id", p.stem), "name": data.get("name", p.stem)})
        except Exception as exc:
            logger.debug("skipping unreadable tenant file %s: %s", p, exc)
            continue
    return {"ok": True, "tenants": tenants, "count": len(tenants)}


@router.post("/register", dependencies=[Depends(require_operator)])
async def register_tenant(payload: dict[str, Any]) -> dict[str, Any]:
    """Register a new tenant."""
    tenant_id = payload.get("id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant id required")

    _tenant_dir().mkdir(parents=True, exist_ok=True)
    path = _tenant_path(tenant_id)
    if path.exists():
        raise HTTPException(status_code=409, detail=f"tenant {tenant_id} already exists")

    record = {
        "id": tenant_id,
        "name": payload.get("name", tenant_id),
        "llm_provider": payload.get("llm_provider", "ollama"),
        "llm_model": payload.get("llm_model", "qwen3:8b"),
        "vault_path": payload.get("vault_path", str(Path.home() / "Documents" / "Vault")),
        "metadata": payload.get("metadata", {}),
    }
    path.write_text(json.dumps(record, indent=2))
    return {"ok": True, "tenant_id": tenant_id}


@router.get("/{tenant_id}")
async def get_tenant(tenant_id: str) -> dict[str, Any]:
    """Get tenant config."""
    path = _tenant_path(tenant_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"tenant {tenant_id} not found")
    data = json.loads(path.read_text())
    return {"ok": True, "tenant": data}


@router.patch("/{tenant_id}", dependencies=[Depends(require_operator)])
async def update_tenant(tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Update tenant config."""
    path = _tenant_path(tenant_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"tenant {tenant_id} not found")
    data = json.loads(path.read_text())
    data.update(payload)
    path.write_text(json.dumps(data, indent=2))
    return {"ok": True, "tenant": data}


@router.delete("/{tenant_id}", dependencies=[Depends(require_operator)])
async def delete_tenant(tenant_id: str) -> dict[str, Any]:
    """Delete tenant."""
    path = _tenant_path(tenant_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"tenant {tenant_id} not found")
    path.unlink()
    return {"ok": True, "deleted": tenant_id}
