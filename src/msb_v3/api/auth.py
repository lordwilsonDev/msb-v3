"""Shared API auth — the gates for every protected surface.

Two mechanisms, one module:

1. ``check_auth`` — the live-auth gate (x-mcp-secret, MCP_BRIDGE_SECRET)
   for the conversation + workflow routers. Enforced when the secret is
   set (CI seeds it and probes send x-mcp-secret — auth is supplied, never
   bypassed); unset secret = dev mode.
2. Phase 3 operator auth — ``bearer_gate`` + ``require_operator``: the
   fail-closed bearer gate (MSB_OPERATOR_TOKEN) for the /governance and
   /flywheel control endpoints. Fail-closed by design (audit smi-017): an
   unconfigured credential closes the surface (503), a mismatch is 401,
   constant-time comparison (secrets.compare_digest). Credentials are read
   live at request time so a config change applies without a restart.

Reads (status, cockpit, turn lists) stay open — only writes that change
system state go through the gates.
"""

from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, Request

from msb_v3.core.config import settings


def check_auth(request: Request) -> None:
    """Live-auth gate, opt-in: enforced when MCP_BRIDGE_SECRET is set; a
    missing/wrong x-mcp-secret is a 401. Unset secret = dev mode."""
    secret = os.getenv("MCP_BRIDGE_SECRET", "")
    if not secret:
        return
    if request.headers.get("x-mcp-secret") != secret:
        raise HTTPException(status_code=401, detail="unauthorized")


def bearer_gate(
    request: Request,
    expected: str,
    closed_detail: str,
    invalid_detail: str = "invalid credentials",
) -> None:
    """Reject unless the request carries ``Authorization: Bearer <expected>``.

    - expected empty  -> 503 (surface closed until configured)
    - mismatch        -> 401
    Bytes comparison (secrets.compare_digest) so the gate never raises on
    exotic header encodings and never leaks timing.
    """
    if not expected:
        raise HTTPException(status_code=503, detail=closed_detail)
    provided = (request.headers.get("authorization") or "").encode()
    expected_b = f"Bearer {expected}".encode()
    if not secrets.compare_digest(provided, expected_b):
        raise HTTPException(status_code=401, detail=invalid_detail)


def require_operator(request: Request) -> None:
    """Fail-closed gate for the /governance and /flywheel control endpoints.

    Add as ``Depends(require_operator)`` on any endpoint that changes
    system state (arm/disarm/approve/turn-start/budget-reset/drill). The
    token is MSB_OPERATOR_TOKEN from .env — unset means the control surface
    is closed, exactly like the /v1 adapter without OPENAI_API_KEY.
    """
    bearer_gate(
        request,
        settings.operator_token,
        "MSB_OPERATOR_TOKEN not configured — control surface closed (set it in .env)",
        "invalid operator token",
    )
