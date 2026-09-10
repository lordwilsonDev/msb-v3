"""Tests for the Phase 1 CapabilityRegistry.

Evidence that the registry is the source of truth for registered capabilities
and that UNKNOWN stays UNKNOWN because the registry returns None for the rest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.agent.safety import RISK_TIERS  # noqa: E402
from msb_v3.governance.capability_registry import (  # noqa: E402
    DOMAIN_FINANCIAL,
    Capability,
    CapabilityRegistry,
    registry_matches_current_tables,
)

# ---------------------------------------------------------------------------
# Known capabilities resolve
# ---------------------------------------------------------------------------

def test_registry_seeded_from_risk_tiers() -> None:
    registry = CapabilityRegistry()
    assert registry_matches_current_tables(registry), (
        "registry must be seeded from the current RISK_TIERS table"
    )


def test_known_capabilities_resolve() -> None:
    registry = CapabilityRegistry()
    for capability_id in RISK_TIERS:
        cap = registry.resolve(capability_id)
        assert cap is not None, f"{capability_id} must be in the registry"
        assert cap.capability_id == capability_id
        assert cap.tier == RISK_TIERS[capability_id]


def test_registry_contains_all_known_capabilities() -> None:
    registry = CapabilityRegistry()
    ids = set(registry.ids())
    assert ids == set(RISK_TIERS.keys()), (
        "registry IDs must match RISK_TIERS keys exactly during the bridge phase"
    )


def test_registry_count_matches_risk_tiers() -> None:
    registry = CapabilityRegistry()
    assert registry.count() == len(RISK_TIERS)


# ---------------------------------------------------------------------------
# Unknown capabilities resolve to None (this is the whole point)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "capability_id",
    [
        "nuke",
        "rm -rf production",
        "destroy the target",
        "remove everything",
        "execute the payload",
        "something_completely_fresh",
        "delete production",
    ],
)
def test_unknown_capability_resolves_to_none(capability_id: str) -> None:
    registry = CapabilityRegistry()
    cap = registry.resolve(capability_id)
    assert cap is None, (
        f"{capability_id!r} is not registered and must resolve to None, "
        f"not to a default tier; got {cap!r}"
    )


def test_unknown_capability_is_not_registered() -> None:
    registry = CapabilityRegistry()
    assert registry.is_registered("nuke") is False
    assert registry.is_registered("rm -rf production") is False
    assert registry.is_registered("read_vault") is True
    assert registry.is_registered("financial") is True


# ---------------------------------------------------------------------------
# Registry is deterministic
# ---------------------------------------------------------------------------

def test_two_registries_are_identical() -> None:
    r1 = CapabilityRegistry()
    r2 = CapabilityRegistry()
    assert r1.ids() == r2.ids()


def test_registry_is_read_only_at_this_stage() -> None:
    registry = CapabilityRegistry()
    # There is intentionally no public mutation API yet. Verifying that the
    # only entry path is the seed keeps the hardening work layered and
    # auditable.
    cap = registry.resolve("read_vault")
    assert cap is not None
    assert cap.enabled is True


# ---------------------------------------------------------------------------
# Capability objects are well-formed
# ---------------------------------------------------------------------------

def test_capability_as_dict_round_trip() -> None:
    registry = CapabilityRegistry()
    cap = registry.resolve("financial")
    assert cap is not None
    d = cap.as_dict()
    assert d["capability_id"] == "financial"
    assert d["tier"] == 4
    assert d["domain"] == DOMAIN_FINANCIAL
    assert d["approval_required"] is True


def test_capability_is_immutable() -> None:
    cap = Capability(
        capability_id="test",
        domain="test",
        tier=1,
        side_effect_class="read_only",
        reversibility="reversible",
        approval_required=False,
        taint_policy="review",
        enabled=True,
        description="test capability",
    )
    # Frozen dataclass — attribute assignment raises.
    with pytest.raises(Exception):
        cap.tier = 99  # type: ignore[maybe-no-member]
