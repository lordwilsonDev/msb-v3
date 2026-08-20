"""Automation router — the brain's control surface.

POST /automation/create {description, approve?} plans a request with
DeepSeek and executes it under the runtime's discipline: dry-run by default
(approve=true is the operator token acting as approval — same rule as the
cron requires_approval jobs), spend capped by MSB_AUTOMATION_BUDGET_USD,
every attempt recorded in the manifest. GET /automation/manifest is the
ledger; GET /automation/status shows budget + which providers are live.

Operator-gated, exactly like /cron: creating automations that fire webhooks
/ send data is a state-changing control surface.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from msb_v3.api.auth import require_operator
from msb_v3.automation.brain import create_automation, plan_automation
from msb_v3.automation.budget import BudgetLedger
from msb_v3.automation.clients import provider_status
from msb_v3.automation.manifest import Manifest
from msb_v3.core.config import settings

router = APIRouter(tags=["automation"])


@router.post("/create", dependencies=[Depends(require_operator)])
def automation_create(body: Dict[str, Any]) -> Dict[str, Any]:
    """Plan + (optionally) create an automation. Without ``approve: true``
    this records a dry-run plan and creates nothing."""
    description = body.get("description") if isinstance(body, dict) else None
    if not isinstance(description, str) or not description.strip():
        raise HTTPException(status_code=422, detail="description is required (non-empty string)")
    approve = bool(body.get("approve", False)) if isinstance(body, dict) else False
    try:
        plan = plan_automation(description)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (RuntimeError, ConnectionError) as exc:
        # The brain is closed until DEEPSEEK_API_KEY is set — fail-closed
        # with the same semantics as the /v1 adapter without a key.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    result = create_automation(plan, approve=approve)
    return {"ok": result.get("ok", False), **result}


@router.get("/manifest", dependencies=[Depends(require_operator)])
def automation_manifest(limit: int = 50) -> Dict[str, Any]:
    """The ledger — every automation attempt and its status."""
    rows = Manifest().list(limit=limit)
    return {"ok": True, "count": len(rows), "manifest": rows}


@router.get("/status", dependencies=[Depends(require_operator)])
def automation_status() -> Dict[str, Any]:
    """Budget remaining + which providers are configured and live."""
    return {
        "ok": True,
        "dry_run": bool(settings.automation_dry_run),
        "budget": BudgetLedger().status(),
        "providers": provider_status(),
    }
