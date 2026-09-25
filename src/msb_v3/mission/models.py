"""Mission records — plain frozen dataclasses.

Blueprint: docs/blueprints/2026-09-25-agent-control-plane.md §4.

A version is identified by its content hash. The parent's hash is part of
the preimage, so the same body built against a different parent is a
different version — that is what makes a stale plan detectable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from msb_ledger.audit_v2 import sha256_hex
from msb_v3.mission.states import MissionState


class VersionKind(str, Enum):
    BLUEPRINT = "blueprint"
    PLAN = "plan"
    TASK_GRAPH = "task_graph"


# Each kind is built against the latest version of its parent kind.
PARENT_KIND: dict[VersionKind, VersionKind | None] = {
    VersionKind.BLUEPRINT: None,
    VersionKind.PLAN: VersionKind.BLUEPRINT,
    VersionKind.TASK_GRAPH: VersionKind.PLAN,
}


def version_sha256(
    kind: VersionKind, parent_sha256: str | None, body: dict[str, Any]
) -> str:
    """Canonical (ledger v2) SHA-256 of kind + parent hash + body.

    Raises ``msb_ledger.audit_v2.CanonicalizationError`` for a body that is
    not canonical JSON (NaN, non-string keys, unsupported types)."""
    return sha256_hex({"kind": kind.value, "parent_sha256": parent_sha256, "body": body})


@dataclass(frozen=True)
class Mission:
    mission_id: str
    objective: str
    created_by: str
    created_at: str
    updated_at: str
    state: MissionState
    resume_state: MissionState | None
    constraints: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "objective": self.objective,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "state": self.state.value,
            "resume_state": self.resume_state.value if self.resume_state else None,
            "constraints": dict(self.constraints),
        }


@dataclass(frozen=True)
class MissionVersion:
    mission_id: str
    kind: VersionKind
    version: int
    sha256: str
    parent_sha256: str | None
    created_by: str
    created_at: str
    body: dict[str, Any]


@dataclass(frozen=True)
class StaleVersion:
    kind: VersionKind
    version: int
    reason: str
