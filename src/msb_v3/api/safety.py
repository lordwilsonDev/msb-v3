"""Safety router module — single router, mounted under multiple prefixes in app factory."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter

router = APIRouter(tags=["safety"])

_SAC_STATE: Dict[str, Any] = {"score": 85, "status": "ok"}
_ECHO_STATE: Dict[str, bool] = {"should_echo": False}
_SCHH_STATE: Dict[str, Any] = {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}
_SYSTEMS_STATE: Dict[str, Any] = {"status": "ok", "components": {}, "ts": datetime.now(timezone.utc).isoformat()}
_NOTIFICATIONS: list = []


@router.get("/status")
async def sac_status() -> dict:
    return _SAC_STATE


@router.post("/status")
async def sac_status_write(body: dict) -> dict:
    _SAC_STATE.update(body)
    return _SAC_STATE


@router.get("/evaluate")
async def echo_evaluate(phase: str = "") -> dict:
    return {"phase": phase, "should_echo": _ECHO_STATE["should_echo"]}


@router.post("/evaluate")
async def echo_evaluate_write(body: dict) -> dict:
    _ECHO_STATE["should_echo"] = bool(body.get("should_echo", False))
    return {"should_echo": _ECHO_STATE["should_echo"]}


@router.get("/health")
async def schh_status() -> dict:
    return _SCHH_STATE


@router.get("/systems")
async def systems_health_status() -> dict:
    return _SYSTEMS_STATE


@router.post("/notify")
async def sn_notify(body: dict) -> dict:
    entry = {
        "source": body.get("source", "unknown"),
        "event": body.get("event", "unknown"),
        "message": body.get("message", ""),
        "priority": body.get("priority", "medium"),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    _NOTIFICATIONS.append(entry)
    return entry
