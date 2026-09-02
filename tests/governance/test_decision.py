"""Tests for the Phase 4 GovernanceDecision.

Evidence that:
- UNKNOWN is a valid decision value
- the canonical contract is stable and validates its fields
- the object can be built from a capability resolution
- the object records what the system decided, not what happened
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.governance.decision import (  # noqa: E402
    ApprovalState,
    ConflictState,
    DecisionValue,
    GovernanceDecision,
    ResolutionMethod,
    TaintState,
    from_capability_resolution,
    unknown_decision,
)
from msb_v3.governance.capability_resolver import CapabilityResolution  # noqa: E402


# ---------------------------------------------------------------------------
# UNKNOWN is a valid decision value
# ---------------------------------------------------------------------------

def test_unknown_is_a_valid_decision() -> None:
    d = GovernanceDecision(decision=DecisionValue.UNKNOWN, reason="test")
    assert d.decision == DecisionValue.UNKNOWN
    assert d.is_unknown() is True
    assert d.is_executable() is False


def test_all_decision_values_are_valid() -> None:
    for value in DecisionValue.all():
        d = GovernanceDecision(decision=value, reason="test")
        assert DecisionValue.is_valid(d.decision)


def test_invalid_decision_value_raises() -> None:
    with pytest.raises(ValueError, match="invalid decision value"):
        GovernanceDecision(decision="MAYBE", reason="test")


# ---------------------------------------------------------------------------
# Canonical contract is stable
# ---------------------------------------------------------------------------

def test_decision_object_has_expected_fields() -> None:
    d = GovernanceDecision(decision=DecisionValue.ALLOW, reason="test")
    d_dict = d.as_dict()
    expected_keys = {
        "decision",
        "capability",
        "tier",
        "resolution_method",
        "confidence",
        "taint",
        "authority",
        "approval",
        "policy",
        "reason",
        "evidence",
        "alternatives",
        "conflicts",
        "verification_required",
    }
    assert set(d_dict.keys()) == expected_keys, (
        "the canonical contract must include all expected fields"
    )


def test_decision_object_defaults_are_explicit() -> None:
    d = GovernanceDecision(decision=DecisionValue.ALLOW, reason="test")
    assert d.capability is None
    assert d.tier is None
    assert d.resolution_method is None
    assert d.confidence == 0.0
    assert d.taint == TaintState.CLEAN
    assert d.authority is None
    assert d.approval == ApprovalState.NOT_REQUIRED
    assert d.policy is None
    assert d.reason == "test"
    assert d.evidence == ()
    assert d.alternatives == ()
    assert d.conflicts == ()
    assert d.verification_required is True


def test_decision_object_records_what_was_decided_not_what_happened() -> None:
    # The decision object records a governance decision, not an outcome.
    # That distinction matters: the receipt will later record the decision
    # chain (REQUESTED -> NORMALIZED -> CAPABILITY RESOLVED -> ... -> AUDITED),
    # and the decision object is one vertebra in that chain.
    d = GovernanceDecision(
        decision=DecisionValue.ALLOW,
        capability="read_vault",
        tier=1,
        reason="resolved read_vault via intent_template",
        evidence=("intent template matched",),
    )
    assert d.is_allow() is True
    assert d.capability == "read_vault"
    assert d.reason == "resolved read_vault via intent_template"


# ---------------------------------------------------------------------------
# Object can be built from a capability resolution
# ---------------------------------------------------------------------------

def test_from_capability_resolution_builds_decision() -> None:
    resolution = CapabilityResolution(
        resolved_capability="read_vault",
        resolution_method="intent_template",
        confidence=1.0,
        alternatives=(),
        ambiguous=False,
        risk_tier=1,
        reason="request explicitly asks to search the vault",
        evidence=("intent template matched",),
    )
    d = from_capability_resolution(resolution, decision=DecisionValue.ALLOW)
    assert d.capability == "read_vault"
    assert d.resolution_method == "intent_template"
    assert d.confidence == 1.0
    assert d.tier == 1
    assert d.decision == DecisionValue.ALLOW
    assert "read_vault" in d.reason


def test_from_capability_resolution_unknown_decision() -> None:
    resolution = CapabilityResolution(
        resolved_capability=None,
        resolution_method="none",
        confidence=0.0,
        alternatives=(),
        ambiguous=True,
        risk_tier=None,
        reason="no registered capability resolved this request",
        evidence=("no registered source resolved the request",),
    )
    d = from_capability_resolution(resolution, decision=DecisionValue.UNKNOWN)
    assert d.decision == DecisionValue.UNKNOWN
    assert d.capability is None
    assert d.confidence == 0.0
    assert d.is_unknown() is True


def test_from_capability_resolution_preserves_evidence() -> None:
    resolution = CapabilityResolution(
        resolved_capability="read_vault",
        resolution_method="intent_template",
        confidence=1.0,
        alternatives=(),
        ambiguous=False,
        risk_tier=1,
        reason="request explicitly asks to search the vault",
        evidence=("intent template matched",),
    )
    d = from_capability_resolution(resolution, decision=DecisionValue.ALLOW)
    assert len(d.evidence) == 1
    assert "intent template matched" in d.evidence


# ---------------------------------------------------------------------------
# Unknown decision helper
# ---------------------------------------------------------------------------

def test_unknown_decision_helper() -> None:
    d = unknown_decision(
        capability=None,
        tier=None,
        resolution_method="none",
        reason="no registered capability resolved this request",
        evidence=("no registered source resolved the request",),
        alternatives=(),
    )
    assert d.decision == DecisionValue.UNKNOWN
    assert d.capability is None
    assert d.tier is None
    assert d.resolution_method == "none"
    assert d.confidence == 0.0
    assert d.is_unknown() is True


def test_unknown_decision_helper_default_reason() -> None:
    d = unknown_decision()
    assert "no registered capability resolved this request" in d.reason


# ---------------------------------------------------------------------------
# Validation of subfields
# ---------------------------------------------------------------------------

def test_invalid_taint_raises() -> None:
    with pytest.raises(ValueError, match="invalid taint value"):
        GovernanceDecision(
            decision=DecisionValue.ALLOW,
            taint="dirty",
            reason="test",
        )


def test_invalid_approval_raises() -> None:
    with pytest.raises(ValueError, match="invalid approval value"):
        GovernanceDecision(
            decision=DecisionValue.ALLOW,
            approval="pending",
            reason="test",
        )


def test_invalid_conflict_raises() -> None:
    with pytest.raises(ValueError, match="invalid conflict value"):
        GovernanceDecision(
            decision=DecisionValue.ALLOW,
            conflicts=("not_a_real_conflict",),
            reason="test",
        )


def test_valid_conflict_passes() -> None:
    d = GovernanceDecision(
        decision=DecisionValue.REVIEW,
        conflicts=(ConflictState.RAG_CONFLICT,),
        reason="test",
    )
    assert d.conflicts == (ConflictState.RAG_CONFLICT,)


# ---------------------------------------------------------------------------
# Decision values are strings and serialize cleanly
# ---------------------------------------------------------------------------

def test_decision_values_are_strings() -> None:
    for value in DecisionValue.all():
        assert isinstance(value, str)


def test_decision_seralizes_to_dict() -> None:
    d = GovernanceDecision(
        decision=DecisionValue.ALLOW,
        capability="read_vault",
        tier=1,
        resolution_method="intent_template",
        confidence=1.0,
        taint=TaintState.CLEAN,
        approval=ApprovalState.NOT_REQUIRED,
        reason="resolved read_vault via intent_template",
        evidence=("intent template matched",),
        alternatives=(),
        conflicts=(),
        verification_required=True,
    )
    d_dict = d.as_dict()
    assert isinstance(d_dict, dict)
    assert d_dict["decision"] == DecisionValue.ALLOW
    assert d_dict["capability"] == "read_vault"
    assert d_dict["confidence"] == 1.0


# ---------------------------------------------------------------------------
# Resolution methods and conflict states are enumerated
# ---------------------------------------------------------------------------

def test_resolution_methods_are_strings() -> None:
    for value in ResolutionMethod.all():
        assert isinstance(value, str)


def test_conflict_states_are_strings() -> None:
    for value in ConflictState.all():
        assert isinstance(value, str)


def test_taint_states_are_strings() -> None:
    for value in TaintState.all():
        assert isinstance(value, str)


def test_approval_states_are_strings() -> None:
    for value in ApprovalState.all():
        assert isinstance(value, str)


# ---------------------------------------------------------------------------
# Decision object is mutable (so later phases can fill fields), but validates
# ---------------------------------------------------------------------------

def test_decision_object_can_be_mutated() -> None:
    d = GovernanceDecision(decision=DecisionValue.UNKNOWN, reason="test")
    d.capability = "read_vault"
    d.tier = 1
    d.resolution_method = "intent_template"
    d.confidence = 1.0
    assert d.capability == "read_vault"
    assert d.tier == 1
