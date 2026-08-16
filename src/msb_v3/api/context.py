"""Context Engine API — operator-gated layered context composition (spec §4.2.3).

Compose a token-budgeted, layered context (L0 system invariants → L7
research) for a task, drawn from the Code Graph, Memory Fabric, skill
registry, and AuditChain. Read-only: it curates what the model sees, it
never mutates. Every response carries the per-layer ledger so callers can
see exactly what fit and what was evicted.

    GET /context/compose?task=...&repo=...&project=...&tech=...&budget_tokens=...
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from msb_v3.api.auth import require_operator
from msb_v3.fabric.context_engine import ContextEngine

router = APIRouter()


@router.get("/compose", dependencies=[Depends(require_operator)])
async def compose(
    task: str,
    tenant: str = "default",
    session: str = "default",
    repo: Optional[str] = None,
    project: Optional[str] = None,
    tech: Optional[str] = None,
    budget_tokens: int = 4000,
) -> Dict[str, Any]:
    if not task.strip():
        raise HTTPException(status_code=422, detail="task is required")
    if len(task) > 4000:
        raise HTTPException(status_code=422, detail="task exceeds 4000 chars")
    try:
        budget = max(200, min(budget_tokens, 20000))
    except (TypeError, ValueError):
        budget = 4000
    pkg = ContextEngine().compose(
        task.strip(),
        tenant=tenant,
        session=session,
        repo=repo or None,
        project=project or None,
        tech=tech or None,
        budget_tokens=budget,
    )
    return {"ok": True, "context": pkg.as_dict()}
