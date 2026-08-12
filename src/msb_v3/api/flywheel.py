"""Flywheel router — the loop's control surface.

POST /flywheel/turn starts a turn as a background task (returns fast; the
turn parks at the first approval or completes on its own). Operator
controls approve/resume mirror the governance controls.

Phase 3: the state-changing endpoints (turn start, approve, resume) require
the operator bearer token (Depends(require_operator), MSB_OPERATOR_TOKEN —
fail-closed 503 until set, 401 on mismatch). Read endpoints (turn lists,
turn state) stay open for the cockpit.

The module-level engine singleton is monkeypatched in tests (governance
pattern) so the whole router runs against tmp-backed state.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from msb_v3.api.auth import require_operator
from msb_v3.flywheel.engine import FlywheelEngine

router = APIRouter(tags=["flywheel"])

_engine = FlywheelEngine()


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


def _run_turn_background(turn_id: str) -> None:
    try:
        _engine.run(turn_id)
    except Exception:  # noqa: BLE001 — background task must not crash the worker
        pass


@router.post("/flywheel/turn", status_code=202, dependencies=[Depends(require_operator)])
async def flywheel_turn(body: dict, background_tasks: BackgroundTasks) -> dict:
    problem = body.get("problem")
    if not isinstance(problem, str) or not problem.strip():
        raise HTTPException(status_code=422, detail="problem is required")
    charger = str(body.get("charger", "stub"))
    if charger not in ("stub", "sovereign"):
        raise HTTPException(status_code=422, detail="charger must be 'stub' or 'sovereign'")
    skill = str(body.get("skill", "") or "")
    turn = _engine.start(problem, charger=charger, skill=skill)
    if turn.status == "BLOCKED":
        raise HTTPException(status_code=503, detail=f"turn blocked by brakes: {turn.notes[-1]}")
    background_tasks.add_task(_run_turn_background, turn.turn_id)
    return {"accepted": True, "turn": _turn_payload(turn)}


@router.get("/flywheel/turns")
async def flywheel_turns() -> dict:
    return {"turns": [_turn_payload(t) for t in _engine.list()]}


@router.get("/flywheel/turns/{turn_id}")
async def flywheel_turn_state(turn_id: str) -> dict:
    turn = _engine.get(turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail=f"unknown turn {turn_id}")
    return _turn_payload(turn)


@router.post("/flywheel/turns/{turn_id}/approve", dependencies=[Depends(require_operator)])
async def flywheel_approve(turn_id: str, body: dict) -> dict:
    operator = str(body.get("operator", "operator") or "operator")
    turn = _engine.approve(turn_id, operator=operator)
    return _turn_payload(turn)


@router.post("/flywheel/turns/{turn_id}/resume", dependencies=[Depends(require_operator)])
async def flywheel_resume(turn_id: str) -> dict:
    try:
        turn = _engine.resume(turn_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _turn_payload(turn)
