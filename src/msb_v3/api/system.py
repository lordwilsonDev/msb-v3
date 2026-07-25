"""System router — routes registry + info."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from msb_v3.core.config import settings

router = APIRouter(tags=["system"])


@router.get("/info")
def system_info() -> Dict[str, Any]:
    return {"service": "msb-v3", "version": "0.1.0"}


@router.get("/health")
def system_health() -> Dict[str, Any]:
    from msb_v3.local_ai.ollama import LocalAIClient
    from msb_v3.observability.metrics import Metrics
    from msb_v3.db.sqlite import Database

    checks: Dict[str, Any] = {"app": "ok", "ready": bool(Metrics._ready)}
    try:
        LocalAIClient().generate("health-check", max_tokens=1)
        checks["ollama"] = "ok"
    except Exception as exc:
        checks["ollama"] = f"error: {exc}"
    try:
        Database().healthcheck()
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
    degraded = any(isinstance(v, str) and v.startswith("error:") for v in checks.values())
    checks["status"] = "degraded" if degraded else "healthy"
    return checks


@router.get("/routes")
def list_routes() -> Dict[str, Any]:
    from msb_v3.api.registry import REGISTRY

    return {"routes": [{"prefix": e["prefix"], "tags": e["tags"]} for e in REGISTRY]}


@router.get("/config")
def system_config() -> Dict[str, Any]:
    from msb_v3.observability.metrics import Metrics

    return {
        "service": "msb-v3",
        "version": "0.1.0",
        "host": settings.host,
        "port": settings.port,
        "ollama_url": settings.ollama_url,
        "ollama_model": settings.ollama_model,
        "db_path": settings.db_path,
        "log_level": settings.log_level,
        "cors_origins": settings.cors_origins,
        "request_timeout_s": settings.request_timeout_s,
        "ready": Metrics._ready,
    }
