"""FastAPI gateway for the Sovereign Node protocol."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from msb_v3.core.rate_limit import RateLimiter
from msb_v3.node.identity import NodeAuthError, ReplayError
from msb_v3.node.models import (
    ChallengeRequest,
    EngageRequest,
    EnrollRequest,
    NodeResponse,
    SessionRequest,
)
from msb_v3.node.service import NodeService, build_service
from msb_v3.vesta.transport import require_vesta_transport

# Router-level transport admission: when MSB_VESTA_REQUIRE_TUNNEL=1, the
# /node/v1 executor surface is reachable only from the allowed private peer
# CIDRs, so the raw signed-executor route is never public.
router = APIRouter(tags=["sovereign-node"], dependencies=[Depends(require_vesta_transport)])
_service: NodeService = build_service()

# Per-client cap on the enrollment/challenge/session handshake: challenge
# rows are created on demand and pruned lazily, so an unauthenticated caller
# who knows a device_id must not be able to spam challenge issuance.
_AUTH_LIMITER = RateLimiter(
    window_s=lambda: 60.0,
    max_count=lambda: 10,
)


def _auth_error(exc: NodeAuthError) -> HTTPException:
    status = 409 if isinstance(exc, ReplayError) else 401
    return HTTPException(status_code=status, detail=str(exc))


def _rate_limit(request: Request) -> None:
    if not _AUTH_LIMITER.check(request, units=1):
        raise HTTPException(status_code=429, detail="too many auth attempts")


@router.get("/status")
async def node_status() -> dict:
    return _service.status()


@router.post("/auth/enroll")
async def enroll(body: EnrollRequest, request: Request) -> dict:
    _rate_limit(request)
    try:
        return _service.enroll(body.device_id, body.public_key, body.pairing_code, body.hardware_assurance)
    except NodeAuthError as exc:
        raise _auth_error(exc) from exc


@router.post("/auth/challenge")
async def challenge(body: ChallengeRequest, request: Request) -> dict:
    _rate_limit(request)
    try:
        return _service.challenge(body.device_id)
    except NodeAuthError as exc:
        raise _auth_error(exc) from exc


@router.post("/auth/session")
async def session(body: SessionRequest, request: Request) -> dict:
    _rate_limit(request)
    try:
        return _service.open_session(body.device_id, body.challenge, body.signature)
    except NodeAuthError as exc:
        raise _auth_error(exc) from exc


@router.post("/engage", response_model=NodeResponse)
async def engage(body: EngageRequest) -> dict:
    try:
        return _service.engage(body.model_dump())
    except NodeAuthError as exc:
        raise _auth_error(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="node execution failed") from exc
