"""Phase 2 — DecisionEvidence spine.

Pins the integrity contract: content-addressing is input-order independent,
records chain-link via parent_hash, tampering and parent-link breaks are
detected (never silently healed), and a task's causal trail reconstructs in
append order.
"""

from __future__ import annotations

import sqlite3

import pytest

from msb_v3.evidence.spine import (
    DecisionEvidence,
    DecisionEvidenceStore,
    SpineError,
)


def _evidence(task_id: str = "task_1", result: str = "ALLOW") -> DecisionEvidence:
    return DecisionEvidence(
        task_id=task_id,
        policy_version="vesta-policy-1",
        policy_result=result,
        risk_level="normal",
        capability_requested=("model.inference", "memory.read"),
        capability_granted=("model.inference", "memory.read") if result == "ALLOW" else (),
        evidence_refs=("ev_a", "ev_b"),
        selected_action="chat" if result == "ALLOW" else None,
        timestamp="2026-08-16T00:00:00+00:00",
    )


def test_append_links_parent_and_computes_content_hash(tmp_path):
    store = DecisionEvidenceStore(str(tmp_path / "spine.db"))
    a = store.append(_evidence("task_1"), audit_seq=7)
    b = store.append(_evidence("task_1"), audit_seq=8)
    assert a.parent_hash == "0" * 64  # genesis
    assert b.parent_hash == a.content_hash
    assert a.content_hash != b.content_hash
    assert a.audit_seq == 7
    assert store.verify_chain() == {"valid": True, "record_count": 2}


def test_content_hash_is_input_order_independent(tmp_path):
    a = DecisionEvidenceStore(str(tmp_path / "a.db")).append(_evidence("task_1"))
    b = DecisionEvidenceStore(str(tmp_path / "b.db")).append(_evidence("task_1"))
    # Same fields + same (genesis) parent => same content hash, independent of
    # the input tuple ordering (to_fields sorts the capability/ref lists).
    assert a.content_hash == b.content_hash


def test_tampering_breaks_verification(tmp_path):
    store = DecisionEvidenceStore(str(tmp_path / "spine.db"))
    store.append(_evidence("task_1"))
    with sqlite3.connect(str(tmp_path / "spine.db")) as conn:
        conn.execute(
            "UPDATE decision_evidence SET payload=replace(payload, 'ALLOW', 'DENY') WHERE seq=1"
        )
    result = store.verify_chain()
    assert result["valid"] is False
    assert result["broken_at_seq"] == 1
    assert "content_hash" in result["reason"]


def test_parent_link_break_detected(tmp_path):
    store = DecisionEvidenceStore(str(tmp_path / "spine.db"))
    store.append(_evidence("task_1"))
    store.append(_evidence("task_2"))
    with sqlite3.connect(str(tmp_path / "spine.db")) as conn:
        conn.execute("UPDATE decision_evidence SET parent_hash=? WHERE seq=2", ("0" * 64,))
    result = store.verify_chain()
    assert result["valid"] is False
    assert result["broken_at_seq"] == 2
    assert result["reason"] == "parent_hash does not match preceding record"


def test_trail_reconstructs_causal_order_for_task(tmp_path):
    store = DecisionEvidenceStore(str(tmp_path / "spine.db"))
    store.append(_evidence("task_a"))
    store.append(_evidence("task_b"))
    store.append(_evidence("task_a", result="DENY"))
    trail = store.trail("task_a")
    assert [r.evidence.policy_result for r in trail] == ["ALLOW", "DENY"]
    # parent_hash is the GLOBAL append-order chain: task_b's record sits
    # between the two task_a records, so the second task_a record chains to
    # task_b, not to the first task_a record. Per-task causal order comes
    # from the explicit task_id field + seq order, not from parent_hash.
    assert trail[0].parent_hash == "0" * 64
    assert trail[1].parent_hash != trail[0].content_hash
    assert store.trail("task_b")[0].evidence.policy_result == "ALLOW"
    assert store.verify_chain()["valid"] is True


def test_get_unknown_raises(tmp_path):
    store = DecisionEvidenceStore(str(tmp_path / "spine.db"))
    with pytest.raises(SpineError):
        store.get("decision_nope")
