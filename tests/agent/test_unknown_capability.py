"""Tests for the Phase 0 UNKNOWN-capability fix.

This file was written to fail under the pre-fix baseline and pass under the
hardened gate. It is the primary regression pin for Invariant-002: an
unknown capability does not inherit Tier 1 / SAFE by default.

See:
- docs/audits/governance-hardening-baseline.md (pre-fix evidence)
- PLAN.md (Phase 0)
- the forensic grill: msb-v3/docs/audits/forensic-grill-2026-09-02.md
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.agent.safety import ActionGate, GateVerdict  # noqa: E402

_UNKNOWN_TIER = -1


@pytest.fixture
def gate() -> ActionGate:
    return ActionGate()


# ---------------------------------------------------------------------------
# Unknown does NOT become SAFE.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "capability",
    [
        "nuke",
        "rm -rf production",
        "destroy the target",
        "remove everything",
        "wipe the environment",
        "execute the payload",
        "take the target offline",
        "eliminate the system",
        "delete production",
        "something_completely_fresh",
    ],
)
def test_unknown_capability_is_not_safe(gate: ActionGate, capability: str) -> None:
    """An unregistered capability must not return SAFE.

    This was the known failure in the baseline: ``ActionGate().gate('nuke')``
    returned ``SAFE / tier 1`` because the default for an unmapped capability
    was ``RISK_TIERS.get(capability, 1)``. After the hardening change, an
    unknown capability returns UNKNOWN (BLOCK disposition, tier=-1), never
    SAFE.
    """
    v = gate.gate(capability)
    assert v.action != "SAFE", (
        f"{capability!r} is not a registered capability and must not be SAFE; "
        f"got {v!r}"
    )

    # The hardened disposition for an unknown, untainted capability is BLOCK.
    assert v.action == "BLOCK", (
        f"{capability!r} must be BLOCKed as an unknown capability; "
        f"got action={v.action!r}"
    )


def test_unknown_capability_tier_is_not_one(gate: ActionGate) -> None:
    """Unknown must not silently inherit Tier 1.

    The baseline behavior was ``tier_of('nuke') == 1``. After the change,
    unknown capabilities report a sentinel tier that is NOT 1 (``-1``), so
    any downstream code that treats tier-1 as "safe" does not accidentally
    treat unknown as safe.
    """
    assert gate.tier_of("nuke") == _UNKNOWN_TIER, (
        "unknown capability tier must be the sentinel, not 1"
    )
    assert gate.tier_of("nuke") != 1


def test_unknown_capability_is_not_registered(gate: ActionGate) -> None:
    """The gate's ``is_registered`` helper reports the truth for unknown
    capabilities. This is the hook derived gates (CapabilityResolver,
    ToolManifest) will use so UNKNOWN stays deterministic and fail-closed.
    """
    assert gate.is_registered("nuke") is False
    assert gate.is_registered("rm -rf production") is False
    assert gate.is_registered("read_vault") is True
    assert gate.is_registered("financial") is True


# ---------------------------------------------------------------------------
# Unknown disposition is deterministic and consistent.
# ---------------------------------------------------------------------------

def test_same_unknown_capability_returns_same_verdict(gate: ActionGate) -> None:
    """Repeating the same unknown capability must produce the same verdict
    every time. Deterministic governance is the point; if the same string
    produces different decisions, the boundary is not a boundary.
    """
    v1 = gate.gate("nuke")
    v2 = gate.gate("nuke")
    assert v1 == v2


def test_unknown_verdict_has_reasonable_fields(gate: ActionGate) -> None:
    v = gate.gate("nuke")
    assert isinstance(v, GateVerdict)
    assert v.allowed is False
    assert v.action == "BLOCK"
    assert v.tier == _UNKNOWN_TIER
    assert v.tainted is False
    assert "not registered" in v.reason.lower() or "unknown" in v.reason.lower()


# ---------------------------------------------------------------------------
# Known capabilities are unchanged.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "capability,expected_action",
    [
        ("read_vault", "SAFE"),
        ("llm_synthesis", "SAFE"),
        ("write_file", "SAFE"),  # tier 2, untainted
        ("vault_delete", "REVIEW"),
        ("send_message", "REVIEW"),
        ("financial", "BLOCK"),
        ("permissions", "BLOCK"),
    ],
)
def test_known_capabilities_unchanged(gate: ActionGate, capability: str, expected_action: str) -> None:
    """The hardening change must not alter known-capability behavior.

    This is an important regression guard: the fix for UNKNOWN must not
    destabilize the existing tier table or the A8 taint path.
    """
    v = gate.gate(capability)
    assert v.action == expected_action, (
        f"{capability!r}: expected {expected_action}, got {v!r}"
    )


# ---------------------------------------------------------------------------
# Taint still escalates for unknown capabilities too.
# ---------------------------------------------------------------------------

def test_untainted_unknown_is_blocked(gate: ActionGate) -> None:
    v = gate.gate("nuke")
    assert v.tainted is False
    assert v.action == "BLOCK"


def test_tainted_unknown_is_review(gate: ActionGate) -> None:
    """If the unknown capability's inputs are tainted, the disposition
    escalates to REVIEW rather than BLOCK, matching the taint axis the gate
    already tracks.
    """
    v = gate.gate("nuke", tainted_inputs=True)
    assert v.tainted is True
    assert v.action == "REVIEW"
    assert "untrusted content" in v.reason.lower()
