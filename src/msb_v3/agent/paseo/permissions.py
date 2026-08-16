"""Paseo permission broker — operator-gated decisions, Vesta-backed.

A Paseo worker's permission request (read/write/shell/plan) does NOT flow
back to the worker. It becomes a durable **Vesta approval** (the same
``VestaApprovalStore`` used for file-write contracts — bind id
``paseo.<agent_id>.<request_id>``, payload sha256 = hash of the request) and
parks the waiting run until an operator decides.

Division of labour — there is exactly one forwarder and it is the waiting
run, not the decider:

    drive_run  parks the request (register) and blocks (wait_for_decision)
    operator   decides via the API (decide) — records the approval, wakes
               the waiter, forwards NOTHING
    drive_run  wakes, sees APPROVED/REJECTED, and forwards the response to
               the daemon itself (allow, or deny+interrupt so the worker
               stops) — a denial never lets the run silently continue

The wait registry is module-level (shared across broker instances) because
the API constructs a fresh broker per request while the run parks on
another instance. Single-process uvicorn (the msb-v3 deployment shape)
makes this sound; a multi-process deployment would need a cross-process
wake channel (out of scope for v1).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from msb_v3.vesta.approvals import ApprovalError, VestaApprovalStore

logger = logging.getLogger(__name__)

# Module-level wait registry — see module docstring.
_WAITS: Dict[str, asyncio.Future] = {}

POLICY_VERSION = "paseo-permission-v1"
DEFAULT_TTL_S = 900


def _bind_id(agent_id: str, request_id: str) -> str:
    return f"paseo.{agent_id}.{request_id}"


def parse_bind(bind_id: str) -> tuple[str, str]:
    """Recover (agent_id, request_id) from a bind id; raise on malformed."""
    parts = bind_id.split(".")
    if len(parts) != 3 or parts[0] != "paseo":
        raise ApprovalError(f"not a paseo permission bind: {bind_id}")
    return parts[1], parts[2]


class PaseoPermissionBroker:
    def __init__(self, approvals: Optional[VestaApprovalStore] = None, *, ttl_s: int = DEFAULT_TTL_S) -> None:
        self.approvals = approvals or VestaApprovalStore()
        self.ttl_s = ttl_s

    async def register(self, agent_id: str, task_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
        """Persist the request as a PENDING Vesta approval and park a waiter.

        Re-parking the same request (a retry after a decision timeout) reuses
        the existing PENDING approval instead of colliding on the unique bind.
        """
        request_id = str(request.get("id") or uuid.uuid4().hex)
        bind = _bind_id(agent_id, request_id)
        try:
            payload = json.dumps(request, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(payload.encode()).hexdigest()
            expires = (datetime.now(timezone.utc) + timedelta(seconds=self.ttl_s)).isoformat()
            approval = self.approvals.submit(
                task_id=task_id or agent_id,
                bind_id=bind,
                target_path=str(request.get("title") or request.get("name") or request_id)[:500],
                payload_evidence_id=f"paseo-perm-{request_id}",
                payload_sha256=digest,
                expected_sha256=digest,
                policy_version=POLICY_VERSION,
                expires_at=expires,
            )
        except ApprovalError:
            existing = [a for a in self.approvals.list(status="PENDING") if a["bind_id"] == bind]
            if not existing:
                raise
            approval = existing[0]
        _WAITS.setdefault(approval["approval_id"], asyncio.get_running_loop().create_future())
        logger.info("paseo permission request parked: %s (%s)", approval["approval_id"], request.get("name"))
        return approval

    def pending(self) -> list[Dict[str, Any]]:
        return self.approvals.list(status="PENDING")

    def get(self, approval_id: str) -> Dict[str, Any]:
        return self.approvals.get(approval_id)

    async def decide(self, approval_id: str, operator: str, approved: bool, message: str = "") -> Dict[str, Any]:
        """Operator decision: record it and wake the parked run.

        Deliberately forwards NOTHING — forwarding is the waiting run's job
        (it holds the daemon handle). A decision stands in the ledger even
        if no run is waiting; the awaiting run applies it when it wakes.
        """
        if approved:
            approval = self.approvals.approve(approval_id, operator)
        else:
            approval = self.approvals.reject(approval_id, operator, reason=message or "denied by operator")
        fut = _WAITS.pop(approval_id, None)
        if fut is not None and not fut.done():
            fut.set_result(approval)
        return approval

    async def wait_for_decision(self, approval_id: str, timeout_s: float) -> Optional[Dict[str, Any]]:
        """Block until the operator decides or the timeout elapses (None)."""
        fut = _WAITS.get(approval_id)
        if fut is None:
            try:
                return self.approvals.get(approval_id)
            except ApprovalError:
                return None
        try:
            return await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            return None
