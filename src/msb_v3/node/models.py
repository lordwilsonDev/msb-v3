"""Pydantic models for the Sovereign Node gateway."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EnrollRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    public_key: str
    pairing_code: str = Field(min_length=1, max_length=256)
    hardware_assurance: str = "software"


class ChallengeRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)


class SessionRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    challenge: str
    signature: str


class EngageRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    timestamp: str
    nonce: str = Field(min_length=16, max_length=256)
    intent: Dict[str, Any]
    signature: str


class NodeResponse(BaseModel):
    request_id: Optional[str] = None
    status: str
    execution_id: Optional[str] = None
    decision: Optional[str] = None
    risk_level: Optional[str] = None
    approval_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    verification: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    audit_event_ids: List[int] = []
