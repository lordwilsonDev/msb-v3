"""Shared API auth — the live-auth gate (x-mcp-secret).

One gate for every router: enforced when MCP_BRIDGE_SECRET is set (CI seeds
it and probes send x-mcp-secret — auth is supplied, never bypassed). Unset
secret = dev mode, mirroring the rest of the app.
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Request


def check_auth(request: Request) -> None:
    """Live-auth gate, opt-in: enforced when MCP_BRIDGE_SECRET is set; a
    missing/wrong x-mcp-secret is a 401. Unset secret = dev mode."""
    secret = os.getenv("MCP_BRIDGE_SECRET", "")
    if not secret:
        return
    if request.headers.get("x-mcp-secret") != secret:
        raise HTTPException(status_code=401, detail="unauthorized")
