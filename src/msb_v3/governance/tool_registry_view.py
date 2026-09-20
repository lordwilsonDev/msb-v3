"""Derived governance view over ``tools/registry.py::TOOLS`` (Deliverable 02 K14).

**The gap this closes.** ``agent/safety.py::RISK_TIERS`` knows 8 capabilities
(``read_vault``, ``write_file``, ``vault_delete``, …). ``tools/registry.py::TOOLS``
— the table the governed tool path actually executes from — declares 3 more that
the risk table has never heard of: ``vault.write``, ``memory.write``,
``factory.run``. For those, a risk-ceiling check (kernel spec §4 guard 7) has no
tier to compare against, so the guard cannot be evaluated at all. The identity
shadow run measured this: 15 of 33 observed calls hit it.

**What this module does NOT do.** It does not extend ``RISK_TIERS`` and it does
not change any gate. ``ActionGate`` reads ``RISK_TIERS`` directly, and an
unrecognised capability there resolves to ``_UNKNOWN_TIER`` →
``_unknown_disposition`` → **BLOCK** (untainted) / REVIEW (tainted). Adding
entries would convert that fail-closed default into a tier-based ALLOW for
capabilities that are reachable from model-supplied task capability names
(``agent/planner.py`` builds ``Task.required_capabilities`` from parsed plan
items, and ``SafeProvider.run_tool`` falls back to ``required_capabilities[0]``).
Widening what a model can cause to execute is a policy decision for the Evidence
Authority, not a side effect of a coverage fix. So this view is *derived* and
*consulted by the kernel/shadow layer only*; the gate's table is untouched.

Two-tier precedence, deterministic:

  1. ``RISK_TIERS`` — the operator-facing decision table, unchanged. Wins.
  2. the capability's declaring tools' ``risk_class`` (``LOW`` → 1, ``MEDIUM`` → 2,
     ``HIGH`` → 3), taking the **maximum** over declaring tools. A capability
     shared by tools of differing declared risk is governed at its highest, so
     ``vault.write`` (declared by 5 MEDIUM tools, 1 LOW, and 1 HIGH) resolves to
     tier 3 — the conservative reading, not the most convenient one.
  3. neither → ``(None, "unknown")``. The caller must treat that as UNKNOWN.
     Exhausting the options must never look like "safe".

The ``risk_class`` → tier mapping is not invented: it is read off the semantics
the tier table already encodes (``docs/governance/capability-registry-v1.md``):
tier 1 = read-only/reversible, tier 2 = local side effect/less recoverable,
tier 3 = irreversible or external side effect, tier 4 = reserved for
``financial``/``permissions``. No declared tool reaches tier 4, and this view
will not invent one.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from msb_v3.agent.safety import RISK_TIERS
from msb_v3.tools.registry import TOOLS

# Declared risk class -> tier. See the docstring: read off the existing tier
# table's semantics, not chosen for convenience.
RISK_CLASS_TO_TIER: Dict[str, int] = {
    "LOW": 1,  # read-only, reversible
    "MEDIUM": 2,  # local side effect, less recoverable
    "HIGH": 3,  # irreversible or external side effect
}

TIER_SOURCE_RISK_TABLE = "risk_table"
TIER_SOURCE_DECLARED = "declared_risk_class"
TIER_SOURCE_UNKNOWN = "unknown"


def declaring_tools(capability: str) -> Tuple[str, ...]:
    """Tool ids in TOOLS that declare ``capability`` as required."""
    return tuple(
        tool_id
        for tool_id, td in TOOLS.items()
        if capability in tuple(getattr(td, "required_capabilities", ()) or ())
    )


def declared_capabilities() -> Tuple[str, ...]:
    """Every capability any registered tool declares, in a stable order."""
    seen: List[str] = []
    for td in TOOLS.values():
        for cap in tuple(getattr(td, "required_capabilities", ()) or ()):
            if cap not in seen:
                seen.append(cap)
    return tuple(seen)


def tier_from_declared_risk_class(capability: str) -> Optional[int]:
    """Highest declared risk tier among the tools requiring ``capability``."""
    tiers = [
        RISK_CLASS_TO_TIER[getattr(TOOLS[tool_id], "risk_class", "")]
        for tool_id in declaring_tools(capability)
        if getattr(TOOLS[tool_id], "risk_class", None) in RISK_CLASS_TO_TIER
    ]
    return max(tiers) if tiers else None


def capability_tier(capability: Optional[str]) -> Tuple[Optional[int], str]:
    """Return ``(tier, source)``. ``source`` is always explicit, never implied.

    ``(None, "unknown")`` is a real answer and callers must not treat it as safe.
    """
    if not capability:
        return None, TIER_SOURCE_UNKNOWN

    known = RISK_TIERS.get(capability)
    if known is not None:
        return known, TIER_SOURCE_RISK_TABLE

    declared = tier_from_declared_risk_class(capability)
    if declared is not None:
        return declared, TIER_SOURCE_DECLARED

    return None, TIER_SOURCE_UNKNOWN


def coverage_report() -> Dict[str, object]:
    """What the two tables jointly cover. For the shadow report, not a decision."""
    rows: List[Dict[str, Any]] = []
    for cap in declared_capabilities():
        tier, source = capability_tier(cap)
        rows.append(
            {
                "capability": cap,
                "tier": tier,
                "source": source,
                "declaring_tools": list(declaring_tools(cap)),
            }
        )
    unknown = [r["capability"] for r in rows if r["source"] == TIER_SOURCE_UNKNOWN]
    ungoverned = [r["capability"] for r in rows if r["source"] != TIER_SOURCE_RISK_TABLE]
    return {
        "risk_table_capabilities": sorted(RISK_TIERS.keys()),
        "declared_by_tools": sorted(declared_capabilities()),
        "capabilities": rows,
        "unknown_tier": sorted(unknown),
        "not_in_risk_table": sorted(ungoverned),
    }
