"""Tool manifests — what each governed tool is allowed to exercise.

Phase 2 deliverable.

Today's ``TOOL_CAPABILITY`` in ``msb_v3.agent.safety`` is a static
``tool -> capability`` mapping. This module generalizes that into a registry
of manifests: each governed tool declares the capability(ies) it may exercise,
its maximum tier, and whether it is enabled.

Design intent:
- Deterministic, no model authority.
- Unknown tools resolve to None (they do not inherit SAFE).
- A tool may not exercise a capability it does not declare. That invariant is
  tested here first, then wired into the executor/gate later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from msb_v3.agent.safety import TOOL_CAPABILITY
from msb_v3.governance.capability_registry import CapabilityRegistry


# ---------------------------------------------------------------------------
# Manifest object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolManifest:
    """What one governed tool may do.

    ``capabilities`` is the set of capability IDs this tool is allowed to
    exercise. If a call wants to exercise a capability outside that set, the
    tool cannot do it through this manifest — that is the manifest mismatch
    invariant.
    """

    tool_id: str
    capabilities: Tuple[str, ...]
    maximum_tier: int
    approval_required: bool
    taint_policy: str
    side_effects: Tuple[str, ...]
    enabled: bool
    version: int = 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "capabilities": list(self.capabilities),
            "maximum_tier": self.maximum_tier,
            "approval_required": self.approval_required,
            "taint_policy": self.taint_policy,
            "side_effects": list(self.side_effects),
            "enabled": self.enabled,
            "version": self.version,
        }

    def may_exercise(self, capability: str) -> bool:
        return capability in self.capabilities

    def may_exercise_any(self) -> bool:
        return bool(self.capabilities)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ToolManifestRegistry:
    """Authoritative inventory of governed tool manifests.

    Initial manifests are seeded from the existing ``TOOL_CAPABILITY`` mapping
    plus the capabilities known to the registry. Unknown tools resolve to
    ``None`` — they do not inherit SAFE.
    """

    def __init__(
        self,
        capability_registry: Optional[CapabilityRegistry] = None,
    ) -> None:
        self._by_id: Dict[str, ToolManifest] = {}
        self._capability_registry = (
            capability_registry if capability_registry is not None else CapabilityRegistry()
        )
        self._seed()

    # ------------------------------------------------------------------
    # Seeding from existing mapping
    # ------------------------------------------------------------------

    def _seed(self) -> None:
        for tool_id, capability_id in TOOL_CAPABILITY.items():
            capabilities = (capability_id,)
            cap = self._capability_registry.resolve(capability_id)
            maximum_tier = cap.tier if cap is not None else 1
            self._put(
                ToolManifest(
                    tool_id=tool_id,
                    capabilities=capabilities,
                    maximum_tier=maximum_tier,
                    approval_required=cap.tier >= 3 if cap is not None else False,
                    taint_policy="review",
                    side_effects=(self._side_effect_for(capability_id, cap),),
                    enabled=True,
                )
            )

    def _side_effect_for(self, capability_id: str, cap: Optional[object]) -> str:
        if cap is None:
            return "unknown"
        # Capability objects carry side_effect_class today.
        try:
            return cap.side_effect_class  # type: ignore[attr-defined]
        except Exception:
            return "unknown"

    def _put(self, manifest: ToolManifest) -> None:
        self._by_id[manifest.tool_id] = manifest

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def lookup(self, tool_id: str) -> Optional[ToolManifest]:
        return self._by_id.get(tool_id)

    def is_registered(self, tool_id: str) -> bool:
        return tool_id in self._by_id

    # ------------------------------------------------------------------
    # Manifest mismatch detection
    # ------------------------------------------------------------------

    def tool_may_exercise(self, tool_id: str, capability: str) -> bool:
        manifest = self.lookup(tool_id)
        if manifest is None:
            return False
        return manifest.may_exercise(capability)

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def all(self) -> Tuple[ToolManifest, ...]:
        return tuple(self._by_id.values())

    def ids(self) -> Tuple[str, ...]:
        return tuple(self._by_id.keys())

    def count(self) -> int:
        return len(self._by_id)


# ---------------------------------------------------------------------------
# Compatibility shim
# ---------------------------------------------------------------------------

def manifest_registry_matches_current_tool_mapping(
    registry: ToolManifestRegistry,
) -> bool:
    """Confirm the manifest registry contains exactly the tools in the current
    ``TOOL_CAPABILITY`` mapping. This is a bridge test, not a permanent
    invariant.
    """
    expected = set(TOOL_CAPABILITY.keys())
    actual = set(registry.ids())
    return expected == actual
