"""System router — routes registry + info."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(tags=["system"])


@router.get("/info")
def system_info() -> Dict[str, Any]:
    return {"service": "msb-v3", "version": "0.1.0"}


@router.get("/routes")
def list_routes() -> Dict[str, Any]:
    from msb_v3.api.registry import REGISTRY

    return {"routes": [{"prefix": e["prefix"], "tags": e["tags"]} for e in REGISTRY]}
