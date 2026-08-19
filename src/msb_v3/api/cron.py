"""Cron router — the /cron control surface for scheduled jobs.

Operator-gated (Depends(require_operator), MSB_OPERATOR_TOKEN — the same
fail-closed rule as /governance and /agent): creating jobs that run actions
against the system is a state-changing control surface and must not be
open. Reads of job definitions are also gated (job bodies carry action
params, which may embed credentials for future external actions).

Manual runs (POST /cron/jobs/{job_id}/run) execute the exact governed path
a scheduled firing uses — kill switch, retries, timeout, receipts — and are
the only way a ``requires_approval`` job ever runs (the schedule skips it;
the operator's token IS the approval).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from msb_v3.api.auth import require_operator
from msb_v3.cron.actions import ACTIONS
from msb_v3.cron.parser import CronExpr
from msb_v3.cron.scheduler import CronScheduler
from msb_v3.cron.store import CronStore

router = APIRouter(tags=["cron"])


def _store() -> CronStore:
    return CronStore()


def _scheduler() -> CronScheduler:
    return CronScheduler(_store())


def _next_run(schedule: str) -> Optional[str]:
    try:
        nxt = CronExpr.parse(schedule).next_run()
        return nxt.isoformat() if nxt else None
    except ValueError:
        return None


def _enrich(job: Dict[str, Any]) -> Dict[str, Any]:
    """Attach the derived next-run time for listings."""
    return {**job, "next_run": _next_run(job["schedule"])}


def _validated_action(action: Any) -> Dict[str, Any]:
    if not isinstance(action, dict) or not isinstance(action.get("type"), str):
        raise HTTPException(status_code=422, detail="action must be an object with a string 'type'")
    if action["type"] not in ACTIONS:
        raise HTTPException(status_code=422, detail=f"unknown action type {action['type']!r} (known: {sorted(ACTIONS)})")
    params = action.get("params")
    if params is not None and not isinstance(params, dict):
        raise HTTPException(status_code=422, detail="action.params must be an object")
    return {"type": action["type"], "params": params or {}}


def _validated_governance(gov: Any) -> Dict[str, Any]:
    if gov is None:
        return {}
    if not isinstance(gov, dict):
        raise HTTPException(status_code=422, detail="governance must be an object")
    out: Dict[str, Any] = {}
    if "requires_approval" in gov and not isinstance(gov["requires_approval"], bool):
        raise HTTPException(status_code=422, detail="governance.requires_approval must be a boolean")
    if "max_retries" in gov:
        try:
            out["max_retries"] = int(gov["max_retries"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="governance.max_retries must be an integer")
    if "timeout_s" in gov:
        try:
            out["timeout_s"] = float(gov["timeout_s"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="governance.timeout_s must be a number")
    if "notify_on_failure" in gov and not isinstance(gov["notify_on_failure"], bool):
        raise HTTPException(status_code=422, detail="governance.notify_on_failure must be a boolean")
    return {**{k: v for k, v in gov.items() if k in ("requires_approval", "notify_on_failure")}, **out}


@router.get("/jobs", dependencies=[Depends(require_operator)])
def list_jobs() -> Dict[str, Any]:
    jobs = [_enrich(j) for j in _store().list_jobs()]
    return {"ok": True, "count": len(jobs), "jobs": jobs}


@router.post("/jobs", dependencies=[Depends(require_operator)])
def create_job(body: Dict[str, Any]) -> Dict[str, Any]:
    job_id = body.get("job_id")
    name = body.get("name")
    schedule = body.get("schedule")
    if not isinstance(job_id, str) or not job_id.strip():
        raise HTTPException(status_code=422, detail="job_id is required (non-empty string)")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=422, detail="name is required (non-empty string)")
    if not isinstance(schedule, str) or not schedule.strip():
        raise HTTPException(status_code=422, detail="schedule is required")
    action = _validated_action(body.get("action"))
    try:
        CronExpr.parse(schedule)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid schedule: {exc}")
    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=422, detail="enabled must be a boolean")
    try:
        job = _store().create_job(
            job_id.strip(),
            name.strip(),
            schedule.strip(),
            action,
            enabled=enabled,
            governance=_validated_governance(body.get("governance")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "job": _enrich(job)}


@router.get("/jobs/{job_id}", dependencies=[Depends(require_operator)])
def get_job(job_id: str) -> Dict[str, Any]:
    try:
        return {"ok": True, "job": _enrich(_store().get_job(job_id))}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/jobs/{job_id}", dependencies=[Depends(require_operator)])
def update_job(job_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for key in ("name", "schedule", "enabled", "action", "governance"):
        if key in body:
            fields[key] = body[key]
    if "schedule" in fields:
        if not isinstance(fields["schedule"], str):
            raise HTTPException(status_code=422, detail="schedule must be a string")
        try:
            CronExpr.parse(fields["schedule"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid schedule: {exc}") from exc
    if "action" in fields:
        fields["action"] = _validated_action(fields["action"])
    if "governance" in fields:
        fields["governance"] = _validated_governance(fields["governance"])
    if "enabled" in fields and not isinstance(fields["enabled"], bool):
        raise HTTPException(status_code=422, detail="enabled must be a boolean")
    if "name" in fields and (not isinstance(fields["name"], str) or not fields["name"].strip()):
        raise HTTPException(status_code=422, detail="name must be a non-empty string")
    try:
        job = _store().update_job(job_id, **fields)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "job": _enrich(job)}


@router.delete("/jobs/{job_id}", dependencies=[Depends(require_operator)])
def delete_job(job_id: str) -> Dict[str, Any]:
    try:
        _store().delete_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "deleted": job_id}


@router.post("/jobs/{job_id}/run", dependencies=[Depends(require_operator)])
async def run_job(job_id: str) -> Dict[str, Any]:
    """Run a job now — the governed path (kill switch, retries, timeout,
    receipts). This is the only execution path for requires_approval jobs:
    the operator token is the approval."""
    scheduler = _scheduler()
    try:
        result = await scheduler.run_job(job_id, trigger="manual")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, **result}


@router.get("/jobs/{job_id}/history", dependencies=[Depends(require_operator)])
def job_history(job_id: str, limit: int = 25) -> Dict[str, Any]:
    try:
        _store().get_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    runs = _store().history(job_id, limit=limit)
    return {"ok": True, "job_id": job_id, "count": len(runs), "runs": runs}


@router.get("/status", dependencies=[Depends(require_operator)])
def cron_status() -> Dict[str, Any]:
    """Scheduler status: enabled flag, tick cadence, job counts, recent run
    outcomes, and any in-flight runs (never hidden)."""
    from msb_v3.core.config import settings

    store = _store()
    runs = store.list_runs(limit=50)
    by_status: Dict[str, int] = {}
    inflight: List[Dict[str, Any]] = []
    for r in runs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        if r["status"] == "RUNNING":
            inflight.append(r)
    jobs = store.list_jobs()
    return {
        "ok": True,
        "enabled": bool(settings.cron_enabled),
        "tick_s": int(settings.cron_tick_s),
        "job_count": len(jobs),
        "enabled_jobs": sum(1 for j in jobs if j["enabled"]),
        "recent_by_status": by_status,
        "inflight": inflight,
    }
