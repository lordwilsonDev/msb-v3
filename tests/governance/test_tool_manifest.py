"""Tests for the Phase 2 ToolManifest.

Evidence that:
- known tools declare the capabilities they exercise
- unknown tools resolve to None (they don't inherit SAFE)
- manifest mismatch is detectable and will later be enforced
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.governance.tool_manifest import (  # noqa: E402
    ToolManifest,
    ToolManifestRegistry,
    manifest_registry_matches_current_tool_mapping,
)
from msb_v3.agent.safety import TOOL_CAPABILITY  # noqa: E402


# ---------------------------------------------------------------------------
# Bridge invariant
# ---------------------------------------------------------------------------

def test_manifest_registry_matches_current_tool_mapping() -> None:
    registry = ToolManifestRegistry()
    assert manifest_registry_matches_current_tool_mapping(registry), (
        "manifest registry must be seeded from the current TOOL_CAPABILITY "
        "mapping during the bridge phase"
    )


def test_manifest_registry_contains_all_known_tools() -> None:
    registry = ToolManifestRegistry()
    assert set(registry.ids()) == set(TOOL_CAPABILITY.keys()), (
        "manifest IDs must match TOOL_CAPABILITY keys exactly during the bridge phase"
    )


def test_manifest_registry_count_matches_tool_mapping() -> None:
    registry = ToolManifestRegistry()
    assert registry.count() == len(TOOL_CAPABILITY)


# ---------------------------------------------------------------------------
# Known tools resolve
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tool_id,expected_capability",
    [
        ("search_query", "read_vault"),
        ("vault_read", "read_vault"),
        ("chat", "llm_synthesis"),
        ("vault_write", "write_file"),
    ],
)
def test_known_tool_resolves(tool_id: str, expected_capability: str) -> None:
    registry = ToolManifestRegistry()
    manifest = registry.lookup(tool_id)
    assert manifest is not None, f"{tool_id} must have a manifest"
    assert manifest.tool_id == tool_id
    assert expected_capability in manifest.capabilities, (
        f"{tool_id} must declare {expected_capability}"
    )
    assert manifest.may_exercise(expected_capability) is True


def test_known_tool_capabilities_are_declared() -> None:
    registry = ToolManifestRegistry()
    for tool_id, capability_id in TOOL_CAPABILITY.items():
        manifest = registry.lookup(tool_id)
        assert manifest is not None
        assert capability_id in manifest.capabilities, (
            f"{tool_id} must declare {capability_id}"
        )


# ---------------------------------------------------------------------------
# Unknown tools resolve to None
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tool_id",
    [
        "no_such_tool",
        "rm_rf_production",
        "nuke_tool",
        "delete_production",
        "something_completely_fresh",
    ],
)
def test_unknown_tool_resolves_to_none(tool_id: str) -> None:
    registry = ToolManifestRegistry()
    manifest = registry.lookup(tool_id)
    assert manifest is None, (
        f"{tool_id!r} is not a declared tool and must resolve to None, "
        f"not to a default SAFE manifest; got {manifest!r}"
    )


def test_unknown_tool_has_no_capabilities() -> None:
    registry = ToolManifestRegistry()
    assert registry.tool_may_exercise("no_such_tool", "read_vault") is False
    assert registry.tool_may_exercise("no_such_tool", "nuke") is False
    assert registry.is_registered("no_such_tool") is False


def test_unknown_tool_does_not_inherit_safe() -> None:
    registry = ToolManifestRegistry()
    # The hardened intent: a tool with no manifest cannot exercise any
    # capability through the manifest registry. This is the UNKNOWN-tool
    # invariant that later phases wire into the executor/gate.
    for capability in ("read_vault", "write_file", "financial", "nuke"):
        assert registry.tool_may_exercise("no_such_tool", capability) is False, (
            f"undeclared tool {tool_id!r} must not be able to exercise {capability}"
        )


# ---------------------------------------------------------------------------
# Manifest mismatch is detectable
# ---------------------------------------------------------------------------

def test_manifest_rejects_undeclared_capability() -> None:
    registry = ToolManifestRegistry()
    vault_write = registry.lookup("vault_write")
    assert vault_write is not None
    assert vault_write.may_exercise("write_file") is True
    assert vault_write.may_exercise("financial") is False, (
        "vault_write must not be able to exercise financial through its manifest"
    )
    assert vault_write.may_exercise("nuke") is False


def test_manifest_has_explicit_maximum_tier() -> None:
    registry = ToolManifestRegistry()
    vault_write = registry.lookup("vault_write")
    assert vault_write is not None
    assert vault_write.maximum_tier == 2, (
        "vault_write exercises write_file (tier 2), so its maximum_tier should be 2"
    )


def test_manifest_has_explicit_enabled_flag() -> None:
    registry = ToolManifestRegistry()
    search_query = registry.lookup("search_query")
    assert search_query is not None
    assert search_query.enabled is True


# ---------------------------------------------------------------------------
# Manifests are well-formed
# ---------------------------------------------------------------------------

def test_manifest_as_dict_round_trip() -> None:
    manifest = ToolManifest(
        tool_id="test_tool",
        capabilities=("read_vault",),
        maximum_tier=1,
        approval_required=False,
        taint_policy="review",
        side_effects=("read_only",),
        enabled=True,
    )
    d = manifest.as_dict()
    assert d["tool_id"] == "test_tool"
    assert d["capabilities"] == ["read_vault"]
    assert d["maximum_tier"] == 1
    assert d["enabled"] is True


def test_manifest_is_immutable() -> None:
    manifest = ToolManifest(
        tool_id="test_tool",
        capabilities=("read_vault",),
        maximum_tier=1,
        approval_required=False,
        taint_policy="review",
        side_effects=("read_only",),
        enabled=True,
    )
    with pytest.raises(Exception):
        manifest.tool_id = "other"  # type: ignore[maybe-no-member]
