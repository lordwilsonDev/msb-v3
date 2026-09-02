"""Capability registry — the authoritative inventory of what MSB-v3 knows.

Phase 1 deliverable. This is the structural source of truth for registered
capabilities, starting from the existing RISK_TIERS + TOOL_CAPABILITY tables
in ``msb_v3.agent.safety``.

The registry answers one question: **does the system know this capability?**
If not, ``resolve(...)`` returns ``None`` and the capability stays UNKNOWN —
it does NOT inherit Tier 1 / SAFE.

Design intent:
- Deterministic, no model authority.
- Read-only at this stage (no mutation API yet; that comes with change control
  in a later phase).
- Seeded from existing tables so the hardening work layers onto the current
  system instead of replacing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from msb_v3.agent.safety import RISK_TIERS, TOOL_CAPABILITY


# ---------------------------------------------------------------------------
# Domain vocabulary
# ---------------------------------------------------------------------------

DOMAIN_VAULT = "vault"
DOMAIN_LLM = "llm_synthesis"
DOMAIN_WEB = "web_search"
DOMAIN_FILE = "file"
DOMAIN_DELETION = "deletion"
DOMAIN_COMMUNICATION = "communication"
DOMAIN_FINANCIAL = "financial"
DOMAIN_PERMISSIONS = "permissions"

DOMAINS: Tuple[str, ...] = (
    DOMAIN_VAULT,
    DOMAIN_LLM,
    DOMAIN_WEB,
    DOMAIN_FILE,
    DOMAIN_DELETION,
    DOMAIN_COMMUNICATION,
    DOMAIN_FINANCIAL,
    DOMAIN_PERMISSIONS,
)

_SIDE_EFFECT_READONLY = "read_only"
_SIDE_EFFECT_LOCAL = "local_side_effect"
_SIDE_EFFECT_EXTERNAL = "external_side_effect"

_REVERSIBLE = "reversible"
_LESS_RECOVERABLE = "less_recoverable"
_IRREVERSIBLE = "irreversible"


# ---------------------------------------------------------------------------
# Capability object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Capability:
    """One registered capability.

    This is a *description* of a capability, not an authorization decision.
    Authorization is a separate layer (the gate, the policy engine, the
    authority engine) that uses this description.
    """

    capability_id: str
    domain: str
    tier: int
    side_effect_class: str
    reversibility: str
    approval_required: bool
    taint_policy: str
    enabled: bool
    description: str
    version: int = 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "domain": self.domain,
            "tier": self.tier,
            "side_effect_class": self.side_effect_class,
            "reversibility": self.reversibility,
            "approval_required": self.approval_required,
            "taint_policy": self.taint_policy,
            "enabled": self.enabled,
            "description": self.description,
            "version": self.version,
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class CapabilityRegistry:
    """Authoritative capability inventory.

    Initial entries are derived from the existing RISK_TIERS + TOOL_CAPABILITY
    tables so the hardening work layers onto the current system. Unknown
    capabilities resolve to None — they do not inherit a default tier.
    """

    def __init__(self) -> None:
        self._by_id: Dict[str, Capability] = {}
        self._seed()

    # ------------------------------------------------------------------
    # Seeding from existing tables
    # ------------------------------------------------------------------

    def _seed(self) -> None:
        # Start from RISK_TIERS and extend each entry into a full Capability.
        # This is the bridge between the current gate and the new registry:
        # same capabilities, richer description, explicit UNKNOWN for the rest.
        for capability_id, tier in RISK_TIERS.items():
            self._put(self._from_tier_table(capability_id, tier))

        # TOOL_CAPABILITY is a tool->capability mapping, not a capability
        # table itself, but it tells us which capabilities the existing tools
        # exercise. Record that mapping separately (used by ToolManifest later).
        # Nothing unsafe happens if a tool declares a capability the registry
        # doesn't know — that becomes the UNKNOWN case, which is exactly the
        # hardening point.

    def _from_tier_table(self, capability_id: str, tier: int) -> Capability:
        domain, side_effect, reversibility, approval, taint, description = (
            _describe_from_tier(capability_id, tier)
        )
        return Capability(
            capability_id=capability_id,
            domain=domain,
            tier=tier,
            side_effect_class=side_effect,
            reversibility=reversibility,
            approval_required=approval,
            taint_policy=taint,
            enabled=True,
            description=description,
        )

    def _put(self, capability: Capability) -> None:
        self._by_id[capability.capability_id] = capability

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def resolve(self, capability_id: str) -> Optional[Capability]:
        """Return the registered capability, or None if unknown.

        This is the central decision point the hardening work builds on: a
        capability not in this registry is UNKNOWN, not SAFE.
        """
        return self._by_id.get(capability_id)

    def is_registered(self, capability_id: str) -> bool:
        return capability_id in self._by_id

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def all(self) -> Tuple[Capability, ...]:
        return tuple(self._by_id.values())

    def count(self) -> int:
        return len(self._by_id)

    def ids(self) -> Tuple[str, ...]:
        return tuple(self._by_id.keys())


# ---------------------------------------------------------------------------
# Deterministic description rules
# ---------------------------------------------------------------------------

def _describe_from_tier(
    capability_id: str, tier: int
) -> Tuple[str, str, str, bool, str, str]:
    """Deterministic mapping from a capability_id + tier into registry fields.

    This exists so the seed is reproducible and auditable. If you want to
    change the description of a capability, change this function or add an
    explicit override — not an ad-hoc branch in the registry.
    """
    if capability_id in {"read_vault", "vault_read"}:
        return (
            DOMAIN_VAULT,
            _SIDE_EFFECT_READONLY,
            _REVERSIBLE,
            False,
            "review",
            "read from the vault / retrieval surface",
        )
    if capability_id in {"llm_synthesis", "chat"}:
        return (
            DOMAIN_LLM,
            _SIDE_EFFECT_READONLY,
            _REVERSIBLE,
            False,
            "review",
            "synthesize text / run model-backed reasoning (no durable side effect)",
        )
    if capability_id in {"web_search", "search_query"}:
        return (
            DOMAIN_WEB,
            _SIDE_EFFECT_READONLY,
            _REVERSIBLE,
            False,
            "review",
            "search external / retrieval sources",
        )
    if capability_id == "write_file":
        return (
            DOMAIN_FILE,
            _SIDE_EFFECT_LOCAL,
            _LESS_RECOVERABLE,
            False,
            "review",
            "write a local file artifact",
        )
    if capability_id == "vault_delete":
        return (
            DOMAIN_DELETION,
            _SIDE_EFFECT_LOCAL,
            _IRREVERSIBLE,
            True,
            "review",
            "delete vaulted content",
        )
    if capability_id == "send_message":
        return (
            DOMAIN_COMMUNICATION,
            _SIDE_EFFECT_EXTERNAL,
            _LESS_RECOVERABLE,
            True,
            "review",
            "send a message / outbound communication",
        )
    if capability_id == "financial":
        return (
            DOMAIN_FINANCIAL,
            _SIDE_EFFECT_EXTERNAL,
            _IRREVERSIBLE,
            True,
            "review",
            "financial capability — consequential, requires explicit authority",
        )
    if capability_id == "permissions":
        return (
            DOMAIN_PERMISSIONS,
            _SIDE_EFFECT_EXTERNAL,
            _IRREVERSIBLE,
            True,
            "review",
            "permissions capability — consequential, requires explicit authority",
        )

    # Defensive fallback: if a tier table entry reaches here, describe it as
    # registered-but-underspecified rather than silently SAFE.
    return (
        "unknown",
        _SIDE_EFFECT_LOCAL,
        _LESS_RECOVERABLE,
        True,
        "review",
        f"registered capability {capability_id!r} with insufficient description",
    )


# ---------------------------------------------------------------------------
# Compatibility shim for the existing gate
# ---------------------------------------------------------------------------

def registry_matches_current_tables(registry: CapabilityRegistry) -> bool:
    """Confirm the registry contains exactly the capabilities the current gate
    knows about. This is a bridge test, not a permanent invariant.

    Once the registry is the source of truth, the gate will consult the registry
    rather than RISK_TIERS directly.
    """
    expected = set(RISK_TIERS.keys())
    actual = set(registry.ids())
    return expected == actual
