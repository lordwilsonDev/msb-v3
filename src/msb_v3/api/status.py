"""Status router — runtime status."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from msb_v3 import __version__
from msb_v3.core.config import settings
from msb_v3.observability.metrics import Metrics

router = APIRouter(tags=["status"])


@router.get("/status")
def status() -> Dict[str, Any]:
    return {
        "service": "msb-v3",
        "version": __version__,
        "host": settings.host,
        "port": settings.port,
        "model": settings.ollama_model,
        "ollama_url": settings.ollama_url,
        "db_path": settings.db_path,
        "ready": Metrics._ready,
    }
