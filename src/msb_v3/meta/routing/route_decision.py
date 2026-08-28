"""RouteDecision — the final routing decision with full audit trail.

A ``RouteDecision`` is the output of the complete routing pipeline:
    capability matching
    probability scoring
    policy filtering
    final selection

It contains the full audit trail so every routing decision is explainable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RouteCandidate:
    """One candidate worker considered during routing."""

    worker_id: str
    capability_score: float = 0.0
    probability_score: float = 0.0
    historical_score: float = 0.0
    cost_score: float = 0.0
    final_score: float = 0.0
    rank: int = 0

    # Audit trail.
    matched_capabilities: List[str] = field(default_factory=list)
    blocked: bool = False
    block_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "capability_score": self.capability_score,
            "probability_score": self.probability_score,
            "historical_score": self.historical_score,
            "cost_score": self.cost_score,
            "final_score": self.final_score,
            "rank": self.rank,
            "blocked": self.blocked,
        }


@dataclass
class RouteDecision:
    """The final routing decision with full audit trail.

    Contains:
        - the selected worker (or None if no eligible worker)
        - all candidates considered
        - the reason for selection or rejection
        - policy checks that were applied
        - the decision timestamp for replay
    """

    task_id: str
    selected_worker_id: Optional[str] = None
    selected_worker_name: str = ""

    # All candidates, ranked.
    candidates: List[RouteCandidate] = field(default_factory=list)

    # Decision metadata.
    reason: str = ""
    policy_checks: List[Dict[str, Any]] = field(default_factory=list)
    fallback_used: bool = False
    escalation_triggered: bool = False

    # Audit.
    decision_at: str = field(default_factory=_now)
    routing_version: str = "v1"
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_selected(self) -> bool:
        """True if a worker was selected."""
        return self.selected_worker_id is not None

    @property
    def rejection_reasons(self) -> List[str]:
        """All reasons candidates were rejected."""
        reasons: List[str] = []
        for c in self.candidates:
            if c.blocked:
                reasons.extend(c.block_reasons)
        return reasons

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "selected_worker_id": self.selected_worker_id,
            "selected_worker_name": self.selected_worker_name,
            "candidates": [c.to_dict() for c in self.candidates],
            "reason": self.reason,
            "policy_checks": self.policy_checks,
            "fallback_used": self.fallback_used,
            "escalation_triggered": self.escalation_triggered,
            "decision_at": self.decision_at,
            "routing_version": self.routing_version,
        }
