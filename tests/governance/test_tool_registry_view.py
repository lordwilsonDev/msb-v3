"""Derived governance view over the tool registry (Deliverable 02 K14).

Covers the coverage fix AND — the load-bearing one — that fixing coverage did
**not** change what the gate will allow. ``ActionGate`` reads ``RISK_TIERS``
directly, so extending that table would convert its fail-closed
unknown-capability BLOCK into a tier-based ALLOW for capabilities reachable from
model-supplied task capability names. That is a policy change for the Evidence
Authority, and `test_risk_tiers_table_is_unchanged` is what stops it happening
by accident while fixing a coverage gap.
"""

from __future__ import annotations

from msb_v3.agent.safety import RISK_TIERS
from msb_v3.governance.tool_registry_view import (
    RISK_CLASS_TO_TIER,
    TIER_SOURCE_DECLARED,
    TIER_SOURCE_RISK_TABLE,
    TIER_SOURCE_UNKNOWN,
    capability_tier,
    coverage_report,
    declared_capabilities,
    declaring_tools,
    tier_from_declared_risk_class,
)
from msb_v3.tools.registry import TOOLS


def test_risk_tiers_table_is_unchanged():
    """The gate's decision table must not have been widened to close a coverage gap.

    If this fails because someone legitimately extended RISK_TIERS, that was a
    change to live authorization behaviour and needs its own signed-off job —
    not a quiet edit alongside a coverage fix.
    """
    assert RISK_TIERS == {
        "read_vault": 1,
        "llm_synthesis": 1,
        "web_search": 1,
        "write_file": 2,
        "vault_delete": 3,
        "send_message": 3,
        "financial": 4,
        "permissions": 4,
    }


def test_every_capability_a_registered_tool_declares_has_an_evaluable_tier():
    """The gap the identity shadow measured: no declared capability may be left
    with an unevaluable ceiling."""
    unevaluable = [
        cap for cap in declared_capabilities() if capability_tier(cap)[0] is None
    ]
    assert unevaluable == [], (
        f"these capabilities are declared by tools in TOOLS but have no tier, so a "
        f"risk-ceiling check cannot be evaluated for them: {unevaluable}"
    )


def test_the_gap_that_was_measured_is_closed():
    """Exactly the three capabilities the shadow run flagged."""
    assert {"vault.write", "memory.write", "factory.run"} <= set(declared_capabilities())
    for cap, expected_tier in (("vault.write", 3), ("memory.write", 2), ("factory.run", 2)):
        tier, source = capability_tier(cap)
        assert tier == expected_tier
        assert source == TIER_SOURCE_DECLARED


def test_shared_capability_takes_the_highest_declared_risk():
    """vault.write is required by five MEDIUM tools, one LOW and one HIGH.
    A capability shared across differing declared risk is governed at its
    highest — the conservative reading, not the convenient one."""
    tools = declaring_tools("vault.write")
    assert "vault_promote_draft" in tools and TOOLS["vault_promote_draft"].risk_class == "HIGH"
    assert tier_from_declared_risk_class("vault.write") == RISK_CLASS_TO_TIER["HIGH"] == 3


def test_operator_set_risk_table_takes_precedence():
    tier, source = capability_tier("write_file")
    assert (tier, source) == (2, TIER_SOURCE_RISK_TABLE)


def test_capability_known_to_neither_table_is_unknown():
    assert capability_tier("escalate.godmode") == (None, TIER_SOURCE_UNKNOWN)
    assert capability_tier("") == (None, TIER_SOURCE_UNKNOWN)
    assert capability_tier(None) == (None, TIER_SOURCE_UNKNOWN)


def test_declared_capabilities_are_stable_and_deduplicated():
    caps = declared_capabilities()
    assert len(caps) == len(set(caps))
    assert tuple(caps) == tuple(declared_capabilities())  # deterministic order


def test_coverage_report_names_what_is_unevaluable():
    report = coverage_report()
    assert report["declared_by_tools"] == ["factory.run", "memory.write", "vault.write"]
    assert report["unknown_tier"] == []
    assert report["not_in_risk_table"] == ["factory.run", "memory.write", "vault.write"]
    # every declared capability points at the tools that justify its tier
    for row in report["capabilities"]:
        assert row["declaring_tools"], f"{row['capability']} has a tier but no declaring tool"
