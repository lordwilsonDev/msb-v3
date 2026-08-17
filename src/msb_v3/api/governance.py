"""Governance router — the /governance control surface for the brakes.

Status/status-drill endpoints plus the operator controls: budget reset,
kill-switch arm/disarm, and the approval queue (submit/approve/reject/
cancel). Module-level singletons are monkeypatched in tests (mcp_bridge
pattern) so the whole router runs against tmp-backed state.

Phase 3: every state-changing endpoint requires the operator bearer token
(Depends(require_operator), MSB_OPERATOR_TOKEN from .env — fail-closed
503 until set, 401 on mismatch). Reads (/status, /budget, /approvals)
stay open. The /check drill is protected too: it spends budget units and
audits, so it is not side-effect-free.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from msb_ledger.chain_anchor import anchored_chain_from_env
from msb_v3.api.auth import require_operator
from msb_v3.governance.approval import (
    APPROVAL_KINDS,
    ApprovalError,
    ApprovalQueue,
    IdempotencyError,
)
from msb_v3.governance.budget import BudgetLedger
from msb_v3.governance.governor import OuroborosGovernor
from msb_v3.governance.guard import Guard, GuardVerdict
from msb_v3.governance.killswitch import KillSwitch

router = APIRouter(tags=["governance"])

# Live singletons (tmp-backed in tests via monkeypatch).
_ledger = BudgetLedger.from_settings()
_switch = KillSwitch()
_queue = ApprovalQueue()
_governor = OuroborosGovernor.from_settings()
_audit = anchored_chain_from_env()
_guard = Guard(_switch, _ledger, _queue, _governor, audit_chain=_audit)


def _body(req: Dict[str, Any], key: str, default: Any = None) -> Any:
    return req.get(key, default)


@router.get("/status")
async def status() -> dict:
    # Fail-closed status: an unreadable governor DB reports a degraded
    # governor payload instead of 500ing the whole status surface (the kill
    # switch already reports fail-closed armed on the same condition).
    try:
        recent = _governor.history()
        governor = {"history_len": len(recent), "recent": recent[:5]}
    except Exception as exc:
        governor = {"error": str(exc), "history_len": 0, "recent": []}
    return {
        "killswitch": _switch.state(),
        "budgets": _ledger.state(),
        "governor": governor,
        "approvals": {
            "pending": len(_queue.pending()),
            "kinds_requiring_approval": list(APPROVAL_KINDS),
        },
    }


@router.get("/budget")
async def budget_state() -> dict:
    return _ledger.state()


@router.post("/budget/reset", dependencies=[Depends(require_operator)])
async def budget_reset(body: dict) -> dict:
    category = _body(body, "category")
    if category is not None and not isinstance(category, str):
        raise HTTPException(status_code=422, detail="category must be a string")
    _ledger.reset(category)
    return {"reset": True, "category": category}


@router.post("/killswitch/arm", dependencies=[Depends(require_operator)])
async def killswitch_arm(body: dict) -> dict:
    operator = str(_body(body, "operator", "operator") or "operator")
    reason = str(_body(body, "reason", "") or "")
    return _switch.arm(operator, reason)


@router.post("/killswitch/disarm", dependencies=[Depends(require_operator)])
async def killswitch_disarm(body: dict) -> dict:
    operator = str(_body(body, "operator", "operator") or "operator")
    return _switch.disarm(operator)


@router.post("/killswitch/scope/arm", dependencies=[Depends(require_operator)])
async def killswitch_scope_arm(body: dict) -> dict:
    """Scoped lockdown (unified-architecture §13): arm the brakes for one
    tenant/agent/task/tool/capability/resource only — the global switch is
    untouched, so ``DISABLE shell_execute`` does not disable ``vault_search``."""
    scope_type = str(_body(body, "scope_type", "") or "")
    scope_id = str(_body(body, "scope_id", "") or "")
    operator = str(_body(body, "operator", "operator") or "operator")
    reason = str(_body(body, "reason", "") or "")
    try:
        return _switch.arm_scope(scope_type, scope_id, operator, reason)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/killswitch/scope/disarm", dependencies=[Depends(require_operator)])
async def killswitch_scope_disarm(body: dict) -> dict:
    scope_type = str(_body(body, "scope_type", "") or "")
    scope_id = str(_body(body, "scope_id", "") or "")
    operator = str(_body(body, "operator", "operator") or "operator")
    try:
        return _switch.disarm_scope(scope_type, scope_id, operator)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/approvals")
async def approvals_list(status: Optional[str] = None) -> dict:
    items = _queue.list(status=status) if status else _queue.list()
    return {
        "items": [
            {
                "id": it.item_id,
                "kind": it.kind,
                "title": it.title,
                "status": it.status,
                "created_at": it.created_at,
                "evidence_refs": it.evidence_refs,
            }
            for it in items
        ]
    }


@router.post("/approvals", status_code=201, dependencies=[Depends(require_operator)])
async def approvals_submit(body: dict) -> dict:
    kind = _body(body, "kind")
    title = _body(body, "title")
    if not isinstance(kind, str) or not isinstance(title, str) or not title:
        raise HTTPException(status_code=422, detail="kind and title are required")
    payload = _body(body, "payload") or {}
    evidence = _body(body, "evidence_refs") or []
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="payload must be an object")
    if not isinstance(evidence, list):
        raise HTTPException(status_code=422, detail="evidence_refs must be a list")
    try:
        item = _queue.submit(kind, title, payload=payload, evidence_refs=evidence)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "id": item.item_id,
        "kind": item.kind,
        "title": item.title,
        "status": item.status,
        "created_at": item.created_at,
        "evidence_refs": item.evidence_refs,
    }


def _decide_endpoint(item_id: str, body: dict, action: str) -> dict:
    operator = str(_body(body, "operator", "operator") or "operator")
    reason = _body(body, "reason")
    try:
        if action == "approve":
            item = _queue.approve(item_id, operator, reason=str(reason) if reason else None)
        elif action == "reject":
            item = _queue.reject(item_id, operator, reason=str(reason) if reason else None)
        else:  # cancel
            item = _queue.cancel(item_id, operator, reason=str(reason) if reason else None)
    except IdempotencyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApprovalError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "id": item.item_id,
        "kind": item.kind,
        "status": item.status,
        "decided_by": item.decided_by,
        "decided_at": item.decided_at,
        "reason": item.reason,
    }


@router.post("/approvals/{item_id}/approve", dependencies=[Depends(require_operator)])
async def approvals_approve(item_id: str, body: dict) -> dict:
    return _decide_endpoint(item_id, body, "approve")


@router.post("/approvals/{item_id}/reject", dependencies=[Depends(require_operator)])
async def approvals_reject(item_id: str, body: dict) -> dict:
    return _decide_endpoint(item_id, body, "reject")


@router.post("/approvals/{item_id}/cancel", dependencies=[Depends(require_operator)])
async def approvals_cancel(item_id: str, body: dict) -> dict:
    return _decide_endpoint(item_id, body, "cancel")


@router.post("/check", dependencies=[Depends(require_operator)])
async def check(body: dict) -> dict:
    """Drill endpoint — run the same gate the flywheel calls, see the verdict.

    Lets an operator (or a test, or the Phase 2 loop) prove the brakes
    halt work without executing anything. Mirrors Guard.check_run's
    arguments: action, kind, budget_units, approval_id, signal.
    Protected (Phase 3): check_run spends budget units and audits refusals,
    so an unauthenticated caller could burn budget through it.
    """
    verdict: GuardVerdict = _guard.check_run(
        action=str(_body(body, "action", "drill")),
        kind=_body(body, "kind"),
        budget_units=_body(body, "budget_units"),
        approval_id=_body(body, "approval_id"),
        signal=_body(body, "signal"),
    )
    return {
        "allowed": verdict.allowed,
        "action": verdict.action,
        "reason": verdict.reason,
        "detail": verdict.detail,
    }
