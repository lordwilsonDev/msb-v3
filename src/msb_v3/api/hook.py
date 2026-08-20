"""The webhook sense — one endpoint, every platform points at it.

External platforms (Make/Zapier/GHL/n8n — anything with a webhook trigger)
POST payloads to ``/hook/<automation_id>``; the payload lands in the wake
inbox tagged with the automation id, and the resident agent decides what it
means on the next wake cycle. This is the perceiver idea made literal: the
platform's only job is pointing here; the thinking stays in msb-v3.

Deliberately NOT operator-gated: platforms cannot send bearer tokens. The
edge is protected by the optional ``MSB_AUTOMATION_HOOK_SECRET`` (when set,
``x-hook-secret`` must match — constant-time) and by bounding the payload.
Unknown automation ids are still queued (the agent can say so) — the
perceiver accepts signals, the brain judges them.
"""

from __future__ import annotations

import json
import secrets
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from msb_v3.core.config import settings
from msb_v3.wake.store import WakeStore

router = APIRouter(tags=["hook"])

_MAX_BODY = 8000


@router.post("/{automation_id}")
async def hook_receive(automation_id: str, request: Request) -> Dict[str, Any]:
    """Receive a webhook signal for an automation and queue it for the wake
    agent. Returns immediately — the agent answers within one wake cycle."""
    automation_id = automation_id.strip()
    if not automation_id or len(automation_id) > 120:
        raise HTTPException(status_code=422, detail="invalid automation_id")

    secret = settings.automation_hook_secret
    if secret:
        provided = request.headers.get("x-hook-secret", "")
        if not secrets.compare_digest(provided, secret):
            raise HTTPException(status_code=401, detail="unauthorized")

    body = (await request.body())[:_MAX_BODY]
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        text = "(empty payload)"
    if text.startswith("{") or text.startswith("["):
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False)[:2000]
        except json.JSONDecodeError:
            text = text[:2000]
    else:
        text = text[:2000]

    row = WakeStore().post(f"webhook signal on {automation_id}: {text}", sender=f"hook:{automation_id}")
    return {"ok": True, "queued": True, "message_id": row["id"], "note": "the resident agent will process this on its next wake"}
