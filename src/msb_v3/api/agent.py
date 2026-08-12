"""Agent router — the Handle-this slice exposed over HTTP (/agent).

The Dream Big Blue vertical slice (intent → plan → execute → verify →
evidence) runs end-to-end in this process via ``agent.handle.handle()``.
Phase 2 live test: a caller can drive a real router decision (R score,
frontier vs local) inside the server process, so the decision lands in
this server's Prometheus registry (/metrics/prometheus) — the slice was
previously only reachable from standalone scripts, whose in-process
metrics never appeared on the live server.

Gate: operator bearer token (Depends(require_operator), MSB_OPERATOR_TOKEN
from .env — fail-closed 503 until set, 401 on mismatch), the same control-
surface rule as /governance. Running the slice executes tools against the
vault tenant, so it is state-changing and must not be open.

Importing this module at server startup also registers the router's
Prometheus counter (model_router.ROUTER_DECISIONS) eagerly, so the metric
exists in /metrics/prometheus before the first decision.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from msb_v3.api.auth import require_operator
from msb_v3.agent.handle import handle

router = APIRouter(tags=["agent"])

_MAX_REQUEST_LEN = 2000


@router.post("/handle", dependencies=[Depends(require_operator)])
async def handle_slice(body: Dict[str, Any]) -> Dict[str, Any]:
    """Run the Handle-this slice end-to-end in this process.

    Body: {request: str, privacy?: bool, approve?: bool, tenant?: str,
           output_dir?: str, session?: str}. `privacy` overrides the
    interpreted intent's privacy flag (drives the router's privacy floor);
    None lets the model decide. Runs tools against the vault tenant, gated
    by the operator token.
    """
    request = body.get("request")
    if not isinstance(request, str) or not request.strip():
        raise HTTPException(status_code=422, detail="request is required (non-empty string)")
    if len(request) > _MAX_REQUEST_LEN:
        raise HTTPException(status_code=422, detail=f"request exceeds {_MAX_REQUEST_LEN} chars")

    privacy = body.get("privacy")
    if privacy is not None and not isinstance(privacy, bool):
        raise HTTPException(status_code=422, detail="privacy must be a boolean")

    approve = body.get("approve") or False
    tenant = body.get("tenant")
    if tenant is not None and not isinstance(tenant, str):
        raise HTTPException(status_code=422, detail="tenant must be a string")
    output_dir = body.get("output_dir")
    if output_dir is not None and not isinstance(output_dir, str):
        raise HTTPException(status_code=422, detail="output_dir must be a string")
    session = body.get("session") or "default"

    result = await handle(
        request,
        tenant=tenant or "wilson-vault",
        approve=bool(approve),
        output_dir=output_dir,
        session=str(session),
        privacy=privacy,
    )
    payload = {
        "ok": result.ok,
        "run_id": result.run_id,
        "verdict": result.verdict,
        "deterministic_hash": result.deterministic_hash,
        "error": result.error,
        "trace": result.trace,
    }
    if not result.ok:
        raise HTTPException(status_code=500, detail=payload)
    return payload
