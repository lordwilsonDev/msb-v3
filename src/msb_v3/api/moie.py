"""MoIE API (spec §3, §23-25; Phase 3) — operator-gated.

POST /moie/analyze runs the Mixture-of-Inversion-Experts pipeline on a
claim and returns the fail-closed decision (verdict, contradictions,
recommended actions, IDS). GET /moie/experts lists the registry.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from msb_v3.api.auth import require_operator

router = APIRouter()

_MAX_CLAIM_LEN = 8000


class AnalyzeRequest(BaseModel):
    claim: str
    domains: Optional[List[str]] = None
    thorough: bool = False
    high_impact: bool = False
    tenant: str = "default"


@router.post("/analyze", dependencies=[Depends(require_operator)])
async def analyze(body: AnalyzeRequest) -> Dict:
    from msb_v3.moie import MoIEController

    claim = body.claim.strip()
    if not claim:
        raise HTTPException(status_code=422, detail="claim is required")
    if len(claim) > _MAX_CLAIM_LEN:
        raise HTTPException(status_code=422, detail=f"claim exceeds {_MAX_CLAIM_LEN} chars")
    decision = MoIEController(tenant=body.tenant).analyze(
        claim,
        context={
            "domains": body.domains or [],
            "thorough": body.thorough,
            "high_impact": body.high_impact,
        },
    )
    return {"ok": True, "decision": decision.as_dict()}


@router.get("/experts", dependencies=[Depends(require_operator)])
async def list_experts() -> Dict:
    from msb_v3.moie import ExpertRegistry

    registry = ExpertRegistry()
    return {"ok": True, "count": len(registry.list()), "experts": registry.list()}
