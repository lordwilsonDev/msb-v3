"""Health router — liveness, readiness, component registry."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict  # noqa: F401 — used in router return annotations

from fastapi import APIRouter, Response

from msb_v3.core.config import settings
from msb_v3.observability.metrics import Metrics

router = APIRouter(tags=["health"])

_COMPONENTS: Dict[str, str] = {
    "ollama": "not-checked",
    "db": "not-checked",
}

_READY_STATE: Dict[str, bool] = {"ready": False}


def set_ready(ready: bool) -> None:
    _READY_STATE["ready"] = ready


def set_component(name: str, state: str) -> None:
    _COMPONENTS[name] = state


@router.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "msb-v3",
        "version": "0.1.0",
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
async def ready(response: Response) -> Dict[str, Any]:
    ok = Metrics._ready and all(s == "ok" for s in _COMPONENTS.values())
    status = 200 if ok else 503
    if status == 503:
        response.status_code = 503
    return {
        "ready": ok,
        "components": _COMPONENTS,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
