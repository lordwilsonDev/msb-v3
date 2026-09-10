"""Canonical governance decision object.

Phase 4 deliverable.

This is the single governance contract shared by:
- the capability resolver (proposes a capability + confidence + evidence)
- the action gate (decides ALLOW / REVIEW / BLOCK / UNKNOWN)
- the evidence receipt (records the decision chain)
- shadow mode (records old vs new decisions)

Design intent:
- UNKNOWN is a first-class decision value, not a tier and not SAFE.
- The object records *what the system decided*, not *what happened*.
- Fields that are not yet meaningful are present as explicit None values with
  a note. That keeps the contract stable as later phases wire authority,
  approval, policy, conflicts, and verification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

if TYPE_CHECKING:
    from msb_v3.governance.capability_resolver import CapabilityResolution

# ---------------------------------------------------------------------------
# Decision vocabulary
# ---------------------------------------------------------------------------

class DecisionValue:
    """Canonical decision values.

    These are the values the governance layer can return. They are strings so
    they serialize cleanly and so tests can assert on them without importing
    the enum machinery.
    """

    ALLOW = "ALLOW"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"
    UNKNOWN = "UNKNOWN"

    _ALL: Tuple[str, ...] = (ALLOW, REVIEW, BLOCK, UNKNOWN)

    @classmethod
    def all(cls) -> Tuple[str, ...]:
        return cls._ALL

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._ALL


class ResolutionMethod:
    """How a capability was resolved."""

    TOOL_MANIFEST = "tool_manifest"
    CAPABILITY_NAME = "capability_name"
    INTENT_TEMPLATE = "intent_template"
    NONE = "none"

    _ALL: Tuple[str, ...] = (TOOL_MANIFEST, CAPABILITY_NAME, INTENT_TEMPLATE, NONE)

    @classmethod
    def all(cls) -> Tuple[str, ...]:
        return cls._ALL

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._ALL


class TaintState:
    """Whether the inputs driving this decision carried untrusted content."""

    CLEAN = "clean"
    TAINTED = "tainted"

    _ALL: Tuple[str, ...] = (CLEAN, TAINTED)

    @classmethod
    def all(cls) -> Tuple[str, ...]:
        return cls._ALL

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._ALL


class ApprovalState:
    """Whether explicit approval was required and whether it was satisfied."""

    NOT_REQUIRED = "not_required"
    REQUIRED_NOT_SATISFIED = "required_not_satisfied"
    REQUIRED_SATISFIED = "required_satisfied"

    _ALL: Tuple[str, ...] = (
        NOT_REQUIRED,
        REQUIRED_NOT_SATISFIED,
        REQUIRED_SATISFIED,
    )

    @classmethod
    def all(cls) -> Tuple[str, ...]:
        return cls._ALL

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._ALL


class ConflictState:
    """Whether evidence or policy conflicted on this decision."""

    NONE = "none"
    RAG_CONFLICT = "rag_conflict"
    POLICY_CONFLICT = "policy_conflict"
    MEMORY_CONFLICT = "memory_conflict"
    CAPABILITY_AMBIGUITY = "capability_ambiguity"

    _ALL: Tuple[str, ...] = (
        NONE,
        RAG_CONFLICT,
        POLICY_CONFLICT,
        MEMORY_CONFLICT,
        CAPABILITY_AMBIGUITY,
    )

    @classmethod
    def all(cls) -> Tuple[str, ...]:
        return cls._ALL

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._ALL


# ---------------------------------------------------------------------------
# Canonical decision object
# ---------------------------------------------------------------------------

@dataclass
class GovernanceDecision:
    """One governance decision.

    This is the canonical contract. Every execution path should eventually
    produce one of these, so the receipt and shadow mode can record the same
    shape regardless of which subsystem made the decision.

    Fields that are not yet wired into the live path are present as explicit
    None values. They are not omitted — omitting them would let the contract
    drift as later phases add them.
    """

    decision: str  # ALLOW | REVIEW | BLOCK | UNKNOWN
    capability: Optional[str] = None
    tier: Optional[int] = None
    resolution_method: Optional[str] = None
    confidence: float = 0.0
    taint: str = TaintState.CLEAN
    authority: Optional[str] = None  # who authorized (or None if not yet wired)
    approval: str = ApprovalState.NOT_REQUIRED
    policy: Optional[str] = None  # which policy governed (or None if not yet wired)
    reason: str = ""
    evidence: Tuple[str, ...] = field(default_factory=tuple)
    alternatives: Tuple[str, ...] = field(default_factory=tuple)
    conflicts: Tuple[str, ...] = field(default_factory=tuple)
    verification_required: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "capability": self.capability,
            "tier": self.tier,
            "resolution_method": self.resolution_method,
            "confidence": self.confidence,
            "taint": self.taint,
            "authority": self.authority,
            "approval": self.approval,
            "policy": self.policy,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "alternatives": list(self.alternatives),
            "conflicts": list(self.conflicts),
            "verification_required": self.verification_required,
        }

    def is_known_decision(self) -> bool:
        return DecisionValue.is_valid(self.decision)

    def is_allow(self) -> bool:
        return self.decision == DecisionValue.ALLOW

    def is_review(self) -> bool:
        return self.decision == DecisionValue.REVIEW

    def is_block(self) -> bool:
        return self.decision == DecisionValue.BLOCK

    def is_unknown(self) -> bool:
        return self.decision == DecisionValue.UNKNOWN

    def is_executable(self) -> bool:
        return self.decision == DecisionValue.ALLOW

    def __post_init__(self) -> None:
        if not DecisionValue.is_valid(self.decision):
            raise ValueError(
                f"invalid decision value {self.decision!r}; must be one of "
                f"{DecisionValue.all()}"
            )
        if not TaintState.is_valid(self.taint):
            raise ValueError(
                f"invalid taint value {self.taint!r}; must be one of "
                f"{TaintState.all()}"
            )
        if not ApprovalState.is_valid(self.approval):
            raise ValueError(
                f"invalid approval value {self.approval!r}; must be one of "
                f"{ApprovalState.all()}"
            )
        if self.conflicts:
            for c in self.conflicts:
                if not ConflictState.is_valid(c):
                    raise ValueError(
                        f"invalid conflict value {c!r}; must be one of "
                        f"{ConflictState.all()}"
                    )


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------

def from_capability_resolution(
    resolution: "CapabilityResolution",  # noqa: F821 — forward ref to resolver type
    *,
    decision: str,
    taint: str = TaintState.CLEAN,
    approval: str = ApprovalState.NOT_REQUIRED,
    reason: Optional[str] = None,
    authority: Optional[str] = None,
    policy: Optional[str] = None,
    conflicts: Tuple[str, ...] = (),
) -> GovernanceDecision:
    """Build a governance decision from a capability resolution.

    This is the bridge between the resolver and the canonical decision object.
    The decision value is supplied by the caller (typically the gate or the
    policy engine), not by the resolver — the resolver proposes, the gate
    decides.
    """
    if reason is None:
        reason = _default_reason(decision, resolution)
    return GovernanceDecision(
        decision=decision,
        capability=resolution.resolved_capability,
        tier=resolution.risk_tier,
        resolution_method=resolution.resolution_method,
        confidence=resolution.confidence,
        taint=taint,
        authority=authority,
        approval=approval,
        policy=policy,
        reason=reason,
        evidence=resolution.evidence,
        alternatives=resolution.alternatives,
        conflicts=conflicts,
    )


def _default_reason(decision: str, resolution: "CapabilityResolution") -> str:  # noqa: F821
    if decision == DecisionValue.ALLOW:
        return (
            f"resolved {resolution.resolved_capability!r} "
            f"via {resolution.resolution_method!r}"
        )
    if decision == DecisionValue.REVIEW:
        return (
            f"resolved {resolution.resolved_capability!r} "
            f"via {resolution.resolution_method!r}; review required"
        )
    if decision == DecisionValue.BLOCK:
        return (
            f"resolved {resolution.resolved_capability!r} "
            f"via {resolution.resolution_method!r}; blocked"
        )
    return (
        f"no registered capability resolved this request "
        f"(method={resolution.resolution_method!r})"
    )


def unknown_decision(
    capability: Optional[str] = None,
    tier: Optional[int] = None,
    resolution_method: Optional[str] = None,
    reason: Optional[str] = None,
    evidence: Tuple[str, ...] = (),
    alternatives: Tuple[str, ...] = (),
) -> GovernanceDecision:
    """Convenience constructor for an UNKNOWN decision."""
    if reason is None:
        reason = (
            "no registered capability resolved this request"
            if reason is None
            else reason
        )
    return GovernanceDecision(
        decision=DecisionValue.UNKNOWN,
        capability=capability,
        tier=tier,
        resolution_method=resolution_method,
        confidence=0.0,
        taint=TaintState.CLEAN,
        approval=ApprovalState.NOT_REQUIRED,
        reason=reason,
        evidence=evidence,
        alternatives=alternatives,
    )
