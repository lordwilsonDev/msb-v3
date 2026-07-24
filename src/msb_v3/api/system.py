"""System router — version, host, ports."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from msb_v3.core.config import settings

router = APIRouter(tags=["system"])


@router.get("/system/info")
async def system_info() -> Dict[str, Any]:
    return {
        "service": "msb-v3",
        "version": "0.1.0",
        "host": settings.host,
        "port": settings.port,
        "model": settings.ollama_model,
        "ollama_url": settings.ollama_url,
        "db_path": settings.db_path,
    }
