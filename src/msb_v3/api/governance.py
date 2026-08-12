"""Governance router — the /governance control surface for the brakes.

Status/status-drill endpoints plus the operator controls: budget reset,
kill-switch arm/disarm, and the approval queue (submit/approve/reject/
cancel). Module-level singletons are monkeypatched in tests (mcp_bridge
pattern) so the whole router runs against tmp-backed state.

NOTE: operator authentication on these control endpoints is deferred to
Phase 3 security hardening; the server binds to loopback and the existing
control surface (safety, triumvirate) is unprotected in the same way.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

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
from msb_v3.uac.audit_chain import AuditChain

router = APIRouter(tags=["governance"])

# Live singletons (tmp-backed in tests via monkeypatch).
_ledger = BudgetLedger.from_settings()
_switch = KillSwitch()
_queue = ApprovalQueue()
_governor = OuroborosGovernor.from_settings()
_audit = AuditChain()
_guard = Guard(_switch, _ledger, _queue, _governor, audit_chain=_audit)


def _body(req: Dict[str, Any], key: str, default: Any = None) -> Any:
    return req.get(key, default)


@router.get("/status")
async def status() -> dict:
    return {
        "killswitch": _switch.state(),
        "budgets": _ledger.state(),
        "governor": {
            "history_len": len(_governor.history()),
            "recent": _governor.history()[:5],
        },
        "approvals": {
            "pending": len(_queue.pending()),
            "kinds_requiring_approval": list(APPROVAL_KINDS),
        },
    }


@router.get("/budget")
async def budget_state() -> dict:
    return _ledger.state()


@router.post("/budget/reset")
async def budget_reset(body: dict) -> dict:
    category = _body(body, "category")
    if category is not None and not isinstance(category, str):
        raise HTTPException(status_code=422, detail="category must be a string")
    _ledger.reset(category)
    return {"reset": True, "category": category}


@router.post("/killswitch/arm")
async def killswitch_arm(body: dict) -> dict:
    operator = str(_body(body, "operator", "operator") or "operator")
    reason = str(_body(body, "reason", "") or "")
    return _switch.arm(operator, reason)


@router.post("/killswitch/disarm")
async def killswitch_disarm(body: dict) -> dict:
    operator = str(_body(body, "operator", "operator") or "operator")
    return _switch.disarm(operator)


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


@router.post("/approvals", status_code=201)
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


@router.post("/approvals/{item_id}/approve")
async def approvals_approve(item_id: str, body: dict) -> dict:
    return _decide_endpoint(item_id, body, "approve")


@router.post("/approvals/{item_id}/reject")
async def approvals_reject(item_id: str, body: dict) -> dict:
    return _decide_endpoint(item_id, body, "reject")


@router.post("/approvals/{item_id}/cancel")
async def approvals_cancel(item_id: str, body: dict) -> dict:
    return _decide_endpoint(item_id, body, "cancel")


@router.post("/check")
async def check(body: dict) -> dict:
    """Drill endpoint — run the same gate the flywheel calls, see the verdict.

    Lets an operator (or a test, or the Phase 2 loop) prove the brakes
    halt work without executing anything. Mirrors Guard.check_run's
    arguments: action, kind, budget_units, approval_id, signal.
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
