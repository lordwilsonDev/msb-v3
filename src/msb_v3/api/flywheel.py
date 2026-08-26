"""Flywheel router — the loop's control surface.

POST /flywheel/turn starts a turn as a background task (returns fast; the
turn parks at the first approval or completes on its own). Operator
controls approve/resume mirror the governance controls.

Phase 3: the state-changing endpoints (turn start, approve, resume) require
the operator bearer token (Depends(require_operator), MSB_OPERATOR_TOKEN —
fail-closed 503 until set, 401 on mismatch). Read endpoints (turn lists,
turn state) stay open for the cockpit.

The engine is resolved through the ApplicationContainer (Phase 1.4); tests
inject a tmp-backed engine by stashing a container on ``app.state.container``
rather than monkeypatching a module-level singleton.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from msb_v3.api.auth import require_operator
from msb_v3.core.container import ApplicationContainer, get_container_dep
from msb_v3.flywheel.engine import FlywheelEngine
from msb_v3.flywheel.health_bridge import read_flywheel_health

logger = logging.getLogger(__name__)
router = APIRouter(tags=["flywheel"])


def _turn_payload(turn) -> dict:
    return {
        "turn_id": turn.turn_id,
        "problem": turn.problem,
        "status": turn.status,
        "stage": turn.stage,
        "charger": turn.charger,
        "skill": turn.skill,
        "novelty": turn.novelty,
        "approval_ids": turn.approval_ids,
        "notes": turn.notes,
        "created_at": turn.created_at,
        "updated_at": turn.updated_at,
        "record_path": turn.record_path,
    }


def _run_turn_background(turn_id: str, engine: FlywheelEngine) -> None:
    try:
        engine.run(turn_id)
    except Exception as exc:
        logger.warning("background flywheel turn %s failed: %s", turn_id, exc)


@router.post("/flywheel/turn", status_code=202, dependencies=[Depends(require_operator)])
async def flywheel_turn(
    body: dict,
    background_tasks: BackgroundTasks,
    container: ApplicationContainer = Depends(get_container_dep),
) -> dict:
    problem = body.get("problem")
    if not isinstance(problem, str) or not problem.strip():
        raise HTTPException(status_code=422, detail="problem is required")
    charger = str(body.get("charger", "stub"))
    if charger not in ("stub", "sovereign"):
        raise HTTPException(status_code=422, detail="charger must be 'stub' or 'sovereign'")
    skill = str(body.get("skill", "") or "")
    engine = container.flywheel
    turn = engine.start(problem, charger=charger, skill=skill)
    if turn.status == "BLOCKED":
        raise HTTPException(status_code=503, detail=f"turn blocked by brakes: {turn.notes[-1]}")
    background_tasks.add_task(_run_turn_background, turn.turn_id, engine)
    return {"accepted": True, "turn": _turn_payload(turn)}


@router.get("/flywheel/turns")
async def flywheel_turns(
    container: ApplicationContainer = Depends(get_container_dep),
) -> dict:
    return {"turns": [_turn_payload(t) for t in container.flywheel.list()]}


@router.get("/flywheel/turns/{turn_id}")
async def flywheel_turn_state(
    turn_id: str,
    container: ApplicationContainer = Depends(get_container_dep),
) -> dict:
    turn = container.flywheel.get(turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail=f"unknown turn {turn_id}")
    return _turn_payload(turn)


@router.post("/flywheel/turns/{turn_id}/approve", dependencies=[Depends(require_operator)])
async def flywheel_approve(
    turn_id: str,
    body: dict,
    container: ApplicationContainer = Depends(get_container_dep),
) -> dict:
    operator = str(body.get("operator", "operator") or "operator")
    turn = container.flywheel.approve(turn_id, operator=operator)
    return _turn_payload(turn)


@router.post("/flywheel/turns/{turn_id}/resume", dependencies=[Depends(require_operator)])
async def flywheel_resume(
    turn_id: str,
    container: ApplicationContainer = Depends(get_container_dep),
) -> dict:
    try:
        turn = container.flywheel.resume(turn_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _turn_payload(turn)


@router.get("/flywheel/health", summary="Real-time flywheel status for dashboards")
async def flywheel_health(request: Request) -> dict:
    """Return flywheel health status derived from Prometheus metrics.

    Read-only: no side effects, no network calls. All data comes from
    metrics that are already being collected by the instrumented engine.
    """
    health = read_flywheel_health()
    result = health.to_dict()
    # Add turn counts from the engine if available
    try:
        container = get_container_dep(request)
        if container and hasattr(container, "flywheel"):
            turns = container.flywheel.list()
            result["total_turns"] = len(turns)
            result["completed_turns"] = sum(1 for t in turns if t.status == "DONE")
            result["active_turns_from_db"] = sum(1 for t in turns if t.status == "RUNNING")
    except Exception:
        result["total_turns"] = None
        result["completed_turns"] = None
    return result
