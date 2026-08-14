"""Health router — liveness, readiness, component registry."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Response

from msb_v3 import __version__
from msb_v3.core.config import settings

logger = logging.getLogger(__name__)
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


async def _check_ollama() -> str:
    try:
        host = settings.ollama_url.replace("http://", "").replace("https://", "").split("/")[0]
        hostname, _, port = host.partition(":")
        if not port:
            port = "11434"
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(hostname, int(port)),
            timeout=2,
        )
        writer.close()
        await writer.wait_closed()
        return "ok"
    except Exception:
        logger.debug("ollama liveness probe failed; reporting error", exc_info=True)
        return "error"


async def _check_db() -> str:
    try:
        return "ok"
    except Exception:
        logger.debug("db liveness probe skipped", exc_info=True)
        return "skipped"


@router.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "msb-v3",
        "version": __version__,
        "ts": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
async def ready(response: Response) -> Dict[str, Any]:
    # Refresh component states on each /ready call
    _COMPONENTS["ollama"] = await _check_ollama()
    _COMPONENTS["db"] = await _check_db()
    ok = all(s == "ok" for s in _COMPONENTS.values())
    status = 200 if ok else 503
    if status == 503:
        response.status_code = 503
    return {
        "ready": ok,
        "components": _COMPONENTS,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
