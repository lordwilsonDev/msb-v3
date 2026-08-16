"""Memory Fabric models — memory types, verification states, item schema.

Mirrors spec §4.2.2: memory types (episodic / semantic / procedural /
architectural), verification states (UNVERIFIED → VERIFIED → CONTRADICTED
→ DEPRECATED), and a provenance-carrying memory item (source_agent,
task_id, importance, decay_factor, verification state, relationships).

Decay model (spec §17): a memory's effective confidence decays over time
by its ``decay_factor`` (per-30-day multiplier), gated by importance and
penalized by contradiction. The fabric computes the *live* score on read
rather than mutating rows on every access — deterministic and cheap.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MemoryType(str, Enum):
    EPISODIC = "episodic"  # what happened
    SEMANTIC = "semantic"  # what is known
    PROCEDURAL = "procedural"  # how to do
    ARCHITECTURAL = "architectural"  # why it works


class VerificationState(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    CONTRADICTED = "CONTRADICTED"
    DEPRECATED = "DEPRECATED"


# Allowed transitions (fail-closed: anything else is rejected).
VERIFICATION_TRANSITIONS: Dict[str, tuple[str, ...]] = {
    "UNVERIFIED": ("VERIFIED", "CONTRADICTED", "DEPRECATED"),
    "VERIFIED": ("CONTRADICTED", "DEPRECATED"),
    "CONTRADICTED": ("VERIFIED", "DEPRECATED"),  # re-verification after contradiction
    "DEPRECATED": (),  # terminal — a deprecated memory cannot be revived
}

# Decay units: a decay_factor of 0.9 means the memory loses 10% of its
# strength per 30 days of inactivity.
_DECAY_PERIOD_S = 30 * 24 * 3600


def recency_factor(decay_factor: float, age_s: float) -> float:
    """The recency multiplier for a memory: 1.0 at age 0, ``decay_factor``
    per 30 days, floor at 0.1 (a memory never fully vanishes from scoring —
    consolidation decides removal)."""
    if decay_factor <= 0:
        return 0.1
    periods = age_s / _DECAY_PERIOD_S
    return max(0.1, decay_factor ** periods)


@dataclass
class MemoryItem:
    memory_id: str
    type: MemoryType
    content: str
    tags: List[str] = field(default_factory=list)
    importance: float = 0.5  # 0.0-1.0
    source_agent: str = ""
    source: str = ""  # provenance: where the memory came from (tool/agent/API)
    task_id: str = ""
    tenant: str = "default"
    project: str = ""
    tech: str = ""
    verification_state: VerificationState = VerificationState.UNVERIFIED
    decay_factor: float = 0.9  # per-30-day multiplier
    relationships: List[str] = field(default_factory=list)  # related memory_ids
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    access_count: int = 0
    archived: bool = False  # soft delete (forget keeps the row, stops recall)

    def live_score(self, now: Optional[float] = None) -> float:
        """Effective retrieval score: importance × recency (spec §17).
        A plain method (not a property) so callers can pass ``now`` for
        deterministic scoring in tests."""
        now = now or time.time()
        age = max(0.0, now - self.last_accessed_at)
        return round(self.importance * recency_factor(self.decay_factor, age), 4)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "type": self.type.value,
            "content": self.content,
            "tags": list(self.tags),
            "importance": self.importance,
            "source_agent": self.source_agent,
            "source": self.source,
            "task_id": self.task_id,
            "tenant": self.tenant,
            "project": self.project,
            "tech": self.tech,
            "verification_state": self.verification_state.value,
            "decay_factor": self.decay_factor,
            "relationships": list(self.relationships),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed_at": self.last_accessed_at,
            "access_count": self.access_count,
            "archived": self.archived,
        }
