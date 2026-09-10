"""Shadow-mode recorder — Phase 5.

Runs the old ActionGate path and the new resolver path in parallel and records
the disagreement, without changing execution. This is where the hardening work
observes whether the deterministic resolver produces useful signal before it is
ever enforced.

Design intent:
- Shadow mode does not control execution.
- Shadow records are persisted to a gitignored location.
- The recorded fields are the canonical governance contract plus the old
  ActionGate verdict, so disagreements can be classified later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from msb_v3.agent.safety import ActionGate
from msb_v3.governance.capability_registry import CapabilityRegistry
from msb_v3.governance.capability_resolver import CapabilityResolver
from msb_v3.governance.decision import DecisionValue, TaintState
from msb_v3.governance.tool_manifest import ToolManifestRegistry

if TYPE_CHECKING:
    from msb_v3.governance.capability_resolver import CapabilityResolution

# ---------------------------------------------------------------------------
# Where shadow records land
# ---------------------------------------------------------------------------

DEFAULT_SHADOW_ROOT = Path("runtime/governance-shadow")
SHADOW_FILE = DEFAULT_SHADOW_ROOT / "shadow.jsonl"

# ---------------------------------------------------------------------------
# One shadow record
# ---------------------------------------------------------------------------


@dataclass
class ShadowRecord:
    """One parallel observation: old gate vs new resolver."""

    request_id: str
    request: str
    ts: float
    tool_name: Optional[str] = None
    taint: str = TaintState.CLEAN

    # Old path — ActionGate directly on the raw string
    old_action: str = ""
    old_tier: int = 0
    old_reason: str = ""
    old_allowed: bool = False

    # New path — resolver + canonical decision
    new_decision: str = ""
    new_capability: Optional[str] = None
    new_tier: Optional[int] = None
    new_resolution_method: Optional[str] = None
    new_confidence: float = 0.0
    new_reason: str = ""
    new_evidence: Tuple[str, ...] = field(default_factory=tuple)
    new_alternatives: Tuple[str, ...] = field(default_factory=tuple)

    # Derived comparison
    disagreement: bool = False
    disagreement_class: str = "pending"
    notes: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "request": self.request,
            "ts": self.ts,
            "tool_name": self.tool_name,
            "taint": self.taint,
            "old_action": self.old_action,
            "old_tier": self.old_tier,
            "old_reason": self.old_reason,
            "old_allowed": self.old_allowed,
            "new_decision": self.new_decision,
            "new_capability": self.new_capability,
            "new_tier": self.new_tier,
            "new_resolution_method": self.new_resolution_method,
            "new_confidence": self.new_confidence,
            "new_reason": self.new_reason,
            "new_evidence": list(self.new_evidence),
            "new_alternatives": list(self.new_alternatives),
            "disagreement": self.disagreement,
            "disagreement_class": self.disagreement_class,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Disagreement classification
# ---------------------------------------------------------------------------

def _classify(old: ShadowRecord, new: ShadowRecord) -> str:
    """Classify the disagreement between the old gate and the new resolver.

    This is the shape of the later disagreement report. The classification is
    intentionally explicit and testable, not a vague "they differ" note.
    """
    if not old.disagreement:
        return "agree"

    # Old SAFE, new not SAFE — the most interesting class.
    if old.old_action == "SAFE" and new.new_decision != DecisionValue.ALLOW:
        if new.new_decision == DecisionValue.UNKNOWN:
            return "OLD_SAFE_NEW_UNKNOWN"
        return "OLD_SAFE_NEW_NOT_SAFE"

    # Old BLOCK, new not BLOCK
    if old.old_action == "BLOCK" and new.new_decision != DecisionValue.BLOCK:
        return "OLD_BLOCK_NEW_NOT_BLOCK"

    # Old REVIEW, new not REVIEW
    if old.old_action == "REVIEW" and new.new_decision != DecisionValue.REVIEW:
        return "OLD_REVIEW_NEW_NOT_REVIEW"

    # New UNKNOWN, old anything
    if new.new_decision == DecisionValue.UNKNOWN:
        return "OLD_NOT_UNKNOWN_NEW_UNKNOWN"

    # New BLOCK, old not BLOCK
    if new.new_decision == DecisionValue.BLOCK and old.old_action != "BLOCK":
        return "OLD_NOT_BLOCK_NEW_BLOCK"

    # New REVIEW, old not REVIEW
    if new.new_decision == DecisionValue.REVIEW and old.old_action != "REVIEW":
        return "OLD_NOT_REVIEW_NEW_REVIEW"

    return "OTHER_DIFFERENCE"


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

class ShadowRecorder:
    """Records old vs new decisions in parallel, without changing execution."""

    def __init__(
        self,
        capability_registry=None,
        tool_manifest_registry=None,
        shadow_root: Optional[Path] = None,
    ) -> None:
        cr = capability_registry
        if cr is None:
            cr = CapabilityRegistry()
        tm = tool_manifest_registry
        if tm is None:
            tm = ToolManifestRegistry(cr)
        self._resolver = CapabilityResolver(
            capability_registry=cr,
            tool_manifest_registry=tm,
        )

        self._capability_registry = self._resolver._capability_registry
        self._tool_manifest_registry = self._resolver._tool_manifest_registry
        self._gate = ActionGate()
        self._shadow_root = shadow_root or DEFAULT_SHADOW_ROOT
        self._shadow_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Single observation
    # ------------------------------------------------------------------

    def record(
        self,
        request_id: str,
        request: str,
        *,
        tool_name: Optional[str] = None,
        taint: str = TaintState.CLEAN,
    ) -> ShadowRecord:
        """Record one old-vs-new observation and persist it."""
        import time as _time

        ts = _time.time()

        # Old path — ActionGate directly on the raw string.
        old_verdict = self._gate.gate(request, tainted_inputs=(taint == TaintState.TAINTED))
        old = ShadowRecord(
            request_id=request_id,
            request=request,
            ts=ts,
            tool_name=tool_name,
            taint=taint,
            old_action=old_verdict.action,
            old_tier=old_verdict.tier,
            old_reason=old_verdict.reason,
            old_allowed=old_verdict.allowed,
            new_decision="",
            new_capability=None,
            new_tier=None,
            new_resolution_method=None,
            new_confidence=0.0,
            new_reason="",
            new_evidence=(),
            new_alternatives=(),
            disagreement=False,
            disagreement_class="pending",
        )
        # New path — resolver + canonical decision.
        resolution = self._resolver.resolve(request, tool_name=tool_name)
        new_capability = resolution.resolved_capability
        new_tier = resolution.risk_tier
        new_reason = resolution.reason
        new_resolution_method = resolution.resolution_method
        new_confidence = resolution.confidence
        new_evidence = resolution.evidence
        new_alternatives = resolution.alternatives
        if resolution.resolved_capability is None:
            new_decision_value = DecisionValue.UNKNOWN
        else:
            new_decision_value = _decision_for_resolution(resolution, taint)

        new_decision = ShadowRecord(
            request_id=request_id,
            request=request,
            ts=ts,
            tool_name=tool_name,
            taint=taint,
            old_action="",
            old_tier=0,
            old_reason="",
            old_allowed=False,
            new_decision=new_decision_value,
            new_capability=new_capability,
            new_tier=new_tier,
            new_resolution_method=new_resolution_method,
            new_confidence=new_confidence,
            new_reason=new_reason,
            new_evidence=new_evidence,
            new_alternatives=new_alternatives,
            disagreement=False,
            disagreement_class="pending",
        )
        # Merge the new-path fields onto the record we return and persist.
        # The comparison below needs them, and callers/readers of the jsonl
        # must see both halves of the observation.
        old.new_decision = new_decision.new_decision
        old.new_capability = new_decision.new_capability
        old.new_tier = new_decision.new_tier
        old.new_resolution_method = new_decision.new_resolution_method
        old.new_confidence = new_decision.new_confidence
        old.new_reason = new_decision.new_reason
        old.new_evidence = new_decision.new_evidence
        old.new_alternatives = new_decision.new_alternatives

        # Merge for comparison.
        old.disagreement = (old.old_action != new_decision.new_decision) or (
            old.old_tier != (new_decision.new_tier or -1)
        )
        if old.disagreement:
            old.disagreement_class = _classify(old, new_decision)
        old.notes = _comparison_notes(old, new_decision)

        self._persist(old)
        return old

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self, record: ShadowRecord) -> None:
        path = self._shadow_root / SHADOW_FILE.name
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.as_dict(), default=str) + "\n")

    # ------------------------------------------------------------------
    # Read-back
    # ------------------------------------------------------------------

    def load(self) -> list[dict]:
        path = self._shadow_root / SHADOW_FILE.name
        if not path.exists():
            return []
        records: list[dict] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decision_for_resolution(
    resolution: "CapabilityResolution",  # noqa: F821
    taint: str,
) -> str:
    """Mirror the old gate's UNKNOWN disposition for shadow comparison.

    This is NOT a new gating decision. It is the shadow-mode mapping of the
    resolver's proposal onto the same ALLOW/REVIEW/BLOCK/UNKNOWN axis the old
    gate uses, so disagreements can be classified. The real gating decision
    comes later, once the resolver is wired into the gate.
    """
    if resolution.resolved_capability is None:
        if taint == TaintState.TAINTED:
            return DecisionValue.REVIEW
        return DecisionValue.BLOCK
    # For a resolved capability, mirror the old tier-based logic so the shadow
    # compares like-with-like on the same axis. This is the shadow comparison
    # helper, not a new gate.
    tier = resolution.risk_tier
    if tier is None:
        return DecisionValue.UNKNOWN
    if tier >= 4:
        return DecisionValue.BLOCK
    if tier >= 3:
        return DecisionValue.REVIEW
    return DecisionValue.ALLOW


def _comparison_notes(old: ShadowRecord, new: ShadowRecord) -> str:
    parts: list[str] = []
    if old.old_action != new.new_decision:
        parts.append(
            f"old={old.old_action} new={new.new_decision}"
        )
    if old.old_tier != (new.new_tier or -1):
        parts.append(
            f"old_tier={old.old_tier} new_tier={new.new_tier}"
        )
    if old.disagreement:
        parts.append(f"class={old.disagreement_class}")
    if not parts:
        parts.append("agree")
    return "; ".join(parts)
