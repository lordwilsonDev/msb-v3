"""Evidence chain — end-to-end verification.

Proves the full governed loop:
1. Action executed → evidence recorded → hash chain intact
2. Tampered evidence is detected (never silently healed)
3. Audit trail is complete for a governed action
4. Replay reconstructs the same story from events + spine

This is the single test that validates the evidence spine is proven,
not just claimed.
"""

from __future__ import annotations

import sqlite3

from msb_v3.evidence.spine import (
    DecisionEvidence,
    DecisionEvidenceStore,
    compute_content_hash,
)
from msb_v3.replay.engine import ReplayEngine
from msb_v3.tasks.lifecycle import TaskLifecycle
from msb_v3.tasks.models import UnifiedTask
from msb_v3.uac.audit_chain import AuditChain

# --- Helpers ---


def _evidence(
    task_id: str = "e2e_task",
    result: str = "ALLOW",
    kind: str = "decision",
) -> DecisionEvidence:
    return DecisionEvidence(
        task_id=task_id,
        policy_version="vesta-policy-1",
        policy_result=result,
        risk_level="normal",
        capability_requested=("model.inference",),
        capability_granted=("model.inference",) if result == "ALLOW" else (),
        evidence_refs=("ev_test",),
        selected_action="chat" if result == "ALLOW" else None,
        timestamp="2026-08-26T00:00:00+00:00",
        kind=kind,
    )


def _task(task_id: str = "e2e_task") -> UnifiedTask:
    return UnifiedTask(
        task_id=task_id,
        kind="agent.run",
        tenant="wilson-vault",
        session="e2e-test",
    )


def _lifecycle(tmp_path) -> TaskLifecycle:
    chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
    return TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)


def _transition_all(lc: TaskLifecycle, task_id: str) -> None:
    """Drive a task through the full lifecycle."""
    lc.create(_task(task_id))
    for state in ("PLANNED", "EXECUTING", "VERIFYING", "COMPLETED"):
        lc.transition(task_id, state)


# --- Tests ---


class TestEvidenceChainE2E:
    """Full governed loop: action → evidence → hash chain → replay."""

    def test_action_recorded_hash_chain_intact(self, tmp_path):
        """1. Append evidence → verify chain is valid → hashes link correctly."""
        store = DecisionEvidenceStore(str(tmp_path / "spine.db"))

        # Simulate: action executed, evidence recorded
        record_a = store.append(_evidence("task_1", "ALLOW"), audit_seq=1)
        record_b = store.append(_evidence("task_1", "ALLOW"), audit_seq=2)
        record_c = store.append(_evidence("task_2", "DENY"), audit_seq=3)

        # Chain is intact
        result = store.verify_chain()
        assert result["valid"] is True
        assert result["record_count"] == 3

        # Hashes link correctly
        assert record_a.parent_hash == "0" * 64  # genesis
        assert record_b.parent_hash == record_a.content_hash
        assert record_c.parent_hash == record_b.content_hash

        # Trail for task_1 has 2 records, task_2 has 1
        trail_1 = store.trail("task_1")
        trail_2 = store.trail("task_2")
        assert len(trail_1) == 2
        assert len(trail_2) == 1

    def test_tampered_evidence_detected(self, tmp_path):
        """2. Tamper with a record → verify chain detects it."""
        store = DecisionEvidenceStore(str(tmp_path / "spine.db"))
        store.append(_evidence("task_1", "ALLOW"), audit_seq=1)
        store.append(_evidence("task_1", "ALLOW"), audit_seq=2)

        # Chain is valid before tampering
        assert store.verify_chain()["valid"] is True

        # Tamper: change ALLOW → DENY in the first record
        with sqlite3.connect(str(tmp_path / "spine.db")) as conn:
            conn.execute(
                "UPDATE decision_evidence SET payload=replace(payload, 'ALLOW', 'DENY') WHERE seq=1"
            )

        # Chain detects the tampering
        result = store.verify_chain()
        assert result["valid"] is False
        assert result["broken_at_seq"] == 1
        assert "content_hash" in result["reason"]

    def test_parent_link_break_detected(self, tmp_path):
        """2b. Break parent linkage → verify chain detects it."""
        store = DecisionEvidenceStore(str(tmp_path / "spine.db"))
        store.append(_evidence("task_1", "ALLOW"), audit_seq=1)
        store.append(_evidence("task_1", "ALLOW"), audit_seq=2)

        # Break the parent link of record 2
        with sqlite3.connect(str(tmp_path / "spine.db")) as conn:
            conn.execute(
                "UPDATE decision_evidence SET parent_hash=? WHERE seq=2",
                ("0" * 64,),
            )

        result = store.verify_chain()
        assert result["valid"] is False
        assert result["broken_at_seq"] == 2
        assert "parent_hash" in result["reason"]

    def test_audit_trail_complete(self, tmp_path):
        """3. Audit trail is complete for a governed action."""
        store = DecisionEvidenceStore(str(tmp_path / "spine.db"))
        lc = _lifecycle(tmp_path)

        # Drive task through lifecycle
        _transition_all(lc, "task_1")

        # Record evidence with audit_seq cross-link
        record = store.append(
            _evidence("task_1", "ALLOW"),
            audit_seq=1,
        )

        # Audit trail is reconstructable
        trail = store.trail("task_1")
        assert len(trail) == 1
        assert trail[0].decision_id == record.decision_id
        assert trail[0].evidence.task_id == "task_1"
        assert trail[0].evidence.policy_result == "ALLOW"
        assert trail[0].audit_seq == 1

        # Content hash is deterministic
        recomputed = compute_content_hash(record.parent_hash, trail[0].evidence)
        assert recomputed == record.content_hash

    def test_replay_reconstructs_same_story(self, tmp_path):
        """4. Replay reconstructs the same story from events + spine."""
        store = DecisionEvidenceStore(str(tmp_path / "spine.db"))
        lc = _lifecycle(tmp_path)

        # Drive task through lifecycle
        _transition_all(lc, "task_1")

        # Record evidence
        store.append(_evidence("task_1", "ALLOW"), audit_seq=1)

        # Replay: state + timeline + decisions
        engine = ReplayEngine(lc, spine=store)
        full = engine.replay_task("task_1")

        # State is consistent
        assert full["consistent"] is True
        assert full["legal"] is True
        assert full["derived_state"] == "COMPLETED"

        # Timeline has events for each transition
        assert len(full["timeline"]) >= 4  # CREATED → PLANNED → EXECUTING → VERIFYING → COMPLETED

        # Decisions are in the spine trail
        assert len(full["decisions"]) == 1
        assert full["decisions"][0]["policy_result"] == "ALLOW"

    def test_multiple_actions_chain_integrity(self, tmp_path):
        """5. Multiple actions across multiple tasks — chain stays intact."""
        store = DecisionEvidenceStore(str(tmp_path / "spine.db"))

        # Interleave tasks
        records = []
        for i in range(10):
            task = f"task_{i % 3}"
            result = "ALLOW" if i % 2 == 0 else "DENY"
            record = store.append(_evidence(task, result), audit_seq=i)
            records.append(record)

        # Chain is valid
        assert store.verify_chain()["valid"] is True
        assert store.verify_chain()["record_count"] == 10

        # Each trail is correct
        for task_name in ("task_0", "task_1", "task_2"):
            trail = store.trail(task_name)
            assert all(r.evidence.task_id == task_name for r in trail)

        # Hashes chain correctly across interleaved tasks
        for i in range(1, len(records)):
            assert records[i].parent_hash == records[i - 1].content_hash

    def test_genesis_hash_is_64_zeros(self, tmp_path):
        """6. First record's parent is the genesis hash."""
        store = DecisionEvidenceStore(str(tmp_path / "spine.db"))
        record = store.append(_evidence("task_first", "ALLOW"), audit_seq=0)
        assert record.parent_hash == "0" * 64

    def test_verify_chain_after_all_appends(self, tmp_path):
        """7. Chain verification is O(n) and catches any break in the full chain."""
        store = DecisionEvidenceStore(str(tmp_path / "spine.db"))

        # Build a chain of 50 records
        records = []
        for i in range(50):
            record = store.append(
                _evidence(f"task_{i}", "ALLOW"),
                audit_seq=i,
            )
            records.append(record)

        # Full chain is valid
        result = store.verify_chain()
        assert result["valid"] is True
        assert result["record_count"] == 50

        # Break record 25 → detection at seq 26 (0-indexed + 1)
        with sqlite3.connect(str(tmp_path / "spine.db")) as conn:
            conn.execute(
                "UPDATE decision_evidence SET payload=replace(payload, 'ALLOW', 'DENY') WHERE seq=26"
            )

        result = store.verify_chain()
        assert result["valid"] is False
        assert result["broken_at_seq"] == 26
