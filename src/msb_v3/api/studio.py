"""Studio router — canonical /status, plus /dashboard folded into the Cockpit.

M3 convergence kept /status here (it absorbed the fields from the never-mounted
api/status.py). The old /dashboard link-card page was a thin duplicate of the
Cockpit, so it now redirects there — the Cockpit is the single read-only
observability surface; /console is the operate-it surface.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from msb_v3 import __version__
from msb_v3.core.config import settings
from msb_v3.observability.metrics import Metrics

router = APIRouter(tags=["studio"])


@router.get("/dashboard")
async def dashboard() -> RedirectResponse:
    """The studio link-card page was folded into the Cockpit."""
    return RedirectResponse(url="/cockpit", status_code=307)


@router.get("/status")
async def status() -> Dict[str, Any]:
    # Canonical /status (M3 convergence 2026-08-16): absorbed the fields from
    # the duplicate api/status.py router (which was never mounted) and deleted
    # that module — one live status route, no dead copy.
    return {
        "service": "msb-v3",
        "version": __version__,
        "ready": Metrics._ready,
        "model": settings.ollama_model,
        "ollama_url": settings.ollama_url,
        "db_path": settings.db_path,
        "host": settings.host,
        "port": settings.port,
    }
