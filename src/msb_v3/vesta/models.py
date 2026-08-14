"""Typed models for the Vesta × MSB integration boundary."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from msb_v3.vesta import VESTA_POLICY_VERSION


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


@dataclass(frozen=True)
class ABind:
    """Immutable authorization context carried through one controlled action."""

    bind_id: str
    task_id: str
    parent_task_id: Optional[str]
    session_id: str
    actor: str
    capabilities: tuple[str, ...]
    risk_class: str
    deadline: str
    cancellation_id: str
    policy_version: str
    evidence_required: bool

    @classmethod
    def create(
        cls,
        session_id: str,
        capabilities: List[str],
        *,
        actor: str = "operator",
        ttl_seconds: int = 120,
    ) -> "ABind":
        now = _now()
        bind_id = f"bind_{uuid.uuid4().hex}"
        return cls(
            bind_id=bind_id,
            task_id=f"task_{uuid.uuid4().hex}",
            parent_task_id=None,
            session_id=session_id,
            actor=actor,
            capabilities=tuple(sorted(set(capabilities))),
            risk_class="normal",
            deadline=_iso(now + timedelta(seconds=ttl_seconds)),
            cancellation_id=f"cancel_{uuid.uuid4().hex}",
            policy_version=VESTA_POLICY_VERSION,
            evidence_required=False,
        )

    def as_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["capabilities"] = list(self.capabilities)
        return value

    def expired(self, now: Optional[datetime] = None) -> bool:
        current = now or _now()
        deadline = datetime.fromisoformat(self.deadline)
        return current >= deadline


class VestaChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    session: str = Field(default="default", min_length=1, max_length=128)
    system: Optional[str] = Field(default=None, max_length=10000)
    capabilities: List[str] = Field(default_factory=lambda: ["model.inference", "memory.read"])


class VestaAuthorizeRequest(BaseModel):
    session: str = Field(default="default", min_length=1, max_length=128)
    capabilities: List[str] = Field(default_factory=lambda: ["model.inference", "memory.read"])


class VestaFileReadRequest(BaseModel):
    session: str = Field(default="default", min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=4096)


class VestaFileWriteRequest(BaseModel):
    session: str = Field(default="default", min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=1_048_576)
    expected_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class VestaShellRequest(BaseModel):
    session: str = Field(default="default", min_length=1, max_length=128)
    executable: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_-]*$")
    args: List[str] = Field(default_factory=list, max_length=32)
    expected_stdout: Optional[str] = Field(default=None, max_length=65536)


class VestaFileReadResponse(BaseModel):
    status: str
    bind_id: str
    task_id: str
    evidence_refs: List[str] = Field(default_factory=list)
    decision: str
    policy_version: str
    result: Dict[str, Any] = Field(default_factory=dict)
    verification: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    audit_event_ids: List[int] = Field(default_factory=list)


class VestaChatResponse(BaseModel):
    ok: bool
    bind_id: str
    task_id: str
    evidence_refs: List[str] = Field(default_factory=list)
    decision: str
    policy_version: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    audit_event_ids: List[int] = Field(default_factory=list)
