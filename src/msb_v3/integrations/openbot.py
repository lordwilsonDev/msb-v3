"""OpenBot <-> MSB v3 adapter.

The adapter deliberately keeps the trust boundary on the MSB side:
OpenBot may request a bounded MSB run and supervisor lifecycle operations, but
it cannot supply shell commands, Docker parameters, or policy overrides.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from msb_v3.agent.handle import handle
from msb_v3.api.auth import require_operator
from msb_v3.core.container import ApplicationContainer, get_container_dep

router = APIRouter(prefix="/openbot", tags=["openbot"])


class OpenBotRunRequest(BaseModel):
    """Inbound message envelope from OpenBot, reduced to MSB's safe run input."""

    bot_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(default="default", min_length=1, max_length=256)
    tenant: str = Field(default="wilson-vault", min_length=1, max_length=128)
    agent_id: str | None = Field(default=None, max_length=128)
    privacy: bool | None = None
    approve: bool = False
    repo: str | None = Field(default=None, max_length=512)


class SupervisorResponse(BaseModel):
    ok: bool
    bot_id: str
    supervisor: dict[str, Any] | list[Any] | None = None


def _base_url() -> str:
    return os.getenv("OPENBOT_SUPERVISOR_URL", "http://127.0.0.1:4300").rstrip("/")


def _token() -> str:
    return os.getenv("OPENBOT_SUPERVISOR_TOKEN", "").strip()


def _supervisor_call(path: str, method: str = "POST") -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{_base_url()}{path}", headers=headers, method=method)
    try:
        with urlopen(request, timeout=float(os.getenv("OPENBOT_TIMEOUT", "10"))) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else {"value": payload}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"OpenBot supervisor error {exc.code}: {detail[:500]}") from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"OpenBot supervisor unavailable: {exc}") from exc


@router.get("/health", dependencies=[Depends(require_operator)])
async def openbot_health() -> dict[str, Any]:
    """Check the configured OpenBot supervisor without touching Docker directly."""
    payload = await asyncio.to_thread(_supervisor_call, "/health", "GET")
    return {"ok": payload.get("status") == "ok", "supervisor": payload}


@router.get("/computers", dependencies=[Depends(require_operator)])
async def list_computers() -> dict[str, Any]:
    return {"ok": True, "supervisor": await asyncio.to_thread(_supervisor_call, "/computers", "GET")}


@router.post("/computers/{bot_id}/ensure", dependencies=[Depends(require_operator)])
async def ensure_computer(bot_id: str) -> dict[str, Any]:
    if not bot_id.strip() or len(bot_id) > 128:
        raise HTTPException(status_code=422, detail="invalid bot_id")
    path = f"/computers/{quote(bot_id, safe='')}/ensure"
    return {"ok": True, "bot_id": bot_id, "supervisor": await asyncio.to_thread(_supervisor_call, path)}


@router.post("/computers/{bot_id}/stop", dependencies=[Depends(require_operator)])
async def stop_computer(bot_id: str) -> dict[str, Any]:
    path = f"/computers/{quote(bot_id, safe='')}/stop"
    return {"ok": True, "bot_id": bot_id, "supervisor": await asyncio.to_thread(_supervisor_call, path)}


@router.post("/computers/{bot_id}/reset", dependencies=[Depends(require_operator)])
async def reset_computer(bot_id: str) -> dict[str, Any]:
    path = f"/computers/{quote(bot_id, safe='')}/reset"
    return {"ok": True, "bot_id": bot_id, "supervisor": await asyncio.to_thread(_supervisor_call, path)}


@router.post("/run", dependencies=[Depends(require_operator)])
async def run_from_openbot(
    body: OpenBotRunRequest,
    container: ApplicationContainer = Depends(get_container_dep),
) -> dict[str, Any]:
    """Translate an OpenBot message into the existing governed MSB handle path."""
    result = await handle(
        body.message,
        tenant=body.tenant,
        approve=body.approve,
        session=body.session_id,
        privacy=body.privacy,
        agent_id=body.agent_id,
        repo=body.repo,
        spine=container.spine,
    )
    return {"ok": result.ok, "bot_id": body.bot_id, "run": asdict(result)}


@router.get("/contract")
async def adapter_contract() -> dict[str, Any]:
    """Machine-readable summary for OpenBot adapter discovery."""
    return {
        "name": "msb-v3-openbot-adapter",
        "version": "1",
        "run": {"method": "POST", "path": "/openbot/run", "auth": "MSB operator bearer"},
        "supervisor": {"base_url_env": "OPENBOT_SUPERVISOR_URL", "token_env": "OPENBOT_SUPERVISOR_TOKEN"},
        "fail_closed": True,
    }


__all__ = ["router", "OpenBotRunRequest"]

