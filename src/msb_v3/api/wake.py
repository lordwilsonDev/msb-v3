"""Wake router — the resident agent's channel from any session.

POST /wake drops a message into the wake inbox; the ``wake-agent`` cron job
(``*/5 * * * *`` by default) picks it up and answers into the outbox. Reads
the outbox from any other session to see the resident agent's reply.

Operator-gated (same fail-closed rule as /cron and /governance): writing a
message the resident agent will act on is a state-changing surface. The
operator token IS the session identity — that's how \"communicate from
another session\" stays authenticated.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from msb_v3.api.auth import require_operator
from msb_v3.core.config import settings
from msb_v3.wake.store import WakeStore

router = APIRouter(tags=["wake"])


def _store() -> WakeStore:
    return WakeStore()


@router.post("", dependencies=[Depends(require_operator)])
def wake_post(body: Dict[str, Any]) -> Dict[str, Any]:
    """Leave a message for the resident agent. It wakes within
    MSB_WAKE_SCHEDULE (default every 5 min) and replies to the outbox."""
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail="text is required (non-empty string)")
    sender = body.get("from") if isinstance(body, dict) else None
    if sender is not None and not isinstance(sender, str):
        raise HTTPException(status_code=422, detail="from must be a string")
    try:
        row = _store().post(text, sender=sender or "operator")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "message": row, "note": f"resident agent wakes on {settings.wake_schedule}"}


@router.get("/outbox", dependencies=[Depends(require_operator)])
def wake_outbox(limit: int = 25) -> Dict[str, Any]:
    """Read the resident agent's replies (newest first)."""
    out = _store().outbox(limit=limit)
    return {"ok": True, "count": len(out), "outbox": out}


@router.get("/status", dependencies=[Depends(require_operator)])
def wake_status() -> Dict[str, Any]:
    """Inbox depth + outbox count + the resident loop's cadence. Seeding the
    cron job happens at server start (app.py lifespan), not on a read."""
    store = _store()
    job_present = False
    try:
        from msb_v3.cron.store import CronStore

        CronStore().get_job("wake-agent")
        job_present = True
    except KeyError:
        pass
    return {
        "ok": True,
        "enabled": bool(settings.wake_enabled),
        "schedule": settings.wake_schedule,
        "max_per_run": int(settings.wake_max_per_run),
        "pending": store.pending_count(),
        "outbox_count": len(store.outbox(limit=1000)),
        "job_present": job_present,
    }
