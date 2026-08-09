"""Tests for uac.audit_chain.AuditChain — must actually detect tampering,
not just accept/return data, since that's the entire point of the module."""
from __future__ import annotations

import json
import sqlite3

from msb_v3.uac.audit_chain import AuditChain, _GENESIS_HASH


def _chain(tmp_path) -> AuditChain:
    return AuditChain(db_path=str(tmp_path / "audit.db"))


def test_first_record_chains_from_genesis(tmp_path):
    chain = _chain(tmp_path)
    record = chain.append("stage_0", "mission_started", {"profession": "bookkeeper"})
    assert record.prev_hash == _GENESIS_HASH
    assert record.seq == 1


def test_records_chain_sequentially(tmp_path):
    chain = _chain(tmp_path)
    r1 = chain.append("stage_0", "event_a", {"x": 1})
    r2 = chain.append("stage_0", "event_b", {"x": 2})
    assert r2.prev_hash == r1.record_hash
    assert r1.record_hash != r2.record_hash


def test_verify_chain_valid_on_untouched_chain(tmp_path):
    chain = _chain(tmp_path)
    chain.append("stage_0", "a", {})
    chain.append("stage_0", "b", {})
    chain.append("stage_0", "c", {})
    result = chain.verify_chain()
    assert result["valid"] is True
    assert result["record_count"] == 3


def test_verify_chain_detects_tampered_payload(tmp_path):
    chain = _chain(tmp_path)
    chain.append("stage_0", "a", {"amount": 100})
    chain.append("stage_0", "b", {})

    # Directly tamper with the stored payload, bypassing the append() API —
    # simulates someone editing the DB file directly.
    with sqlite3.connect(chain.db_path) as conn:
        conn.execute("UPDATE audit_records SET payload=? WHERE seq=1", ('{"amount": 999999}',))

    result = chain.verify_chain()
    assert result["valid"] is False
    assert result["broken_at_seq"] == 1


def test_verify_chain_detects_broken_prev_hash_link(tmp_path):
    chain = _chain(tmp_path)
    chain.append("stage_0", "a", {})
    chain.append("stage_0", "b", {})

    with sqlite3.connect(chain.db_path) as conn:
        conn.execute("UPDATE audit_records SET prev_hash=? WHERE seq=2", ("deadbeef" * 8,))

    result = chain.verify_chain()
    assert result["valid"] is False
    assert result["broken_at_seq"] == 2


def test_get_chain_filters_by_component(tmp_path):
    chain = _chain(tmp_path)
    chain.append("stage_0", "a", {})
    chain.append("stage_1", "b", {})
    chain.append("stage_0", "c", {})

    stage_0_records = chain.get_chain(component="stage_0")
    assert len(stage_0_records) == 2
    assert all(r.component == "stage_0" for r in stage_0_records)

    all_records = chain.get_chain()
    assert len(all_records) == 3


# --- quarantine + repair (hygiene h07, issue #1) ----------------------------


def _tamper_seq(chain, seq: int) -> None:
    with sqlite3.connect(chain.db_path) as conn:
        conn.execute(
            "UPDATE audit_records SET payload=? WHERE seq=?",
            (json.dumps({"n": "TAMPERED"}), seq),
        )


def test_quarantine_marks_broken_chain(tmp_path):
    chain = _chain(tmp_path)
    for i in range(5):
        chain.append("stage_0", "event", {"n": i})
    _tamper_seq(chain, 3)

    result = chain.quarantine()
    assert result["quarantined"] is True
    assert result["broken_at_seq"] == 3
    assert chain._get_meta("state") == "quarantined"
    # quarantine does NOT silently heal — chain must still be detected broken
    assert chain.verify_chain()["valid"] is False


def test_quarantine_noop_on_valid_chain(tmp_path):
    chain = _chain(tmp_path)
    chain.append("stage_0", "a", {})
    result = chain.quarantine()
    assert result["quarantined"] is False
    assert chain._get_meta("state") == "active"


def test_repair_restores_chain_and_is_auditable(tmp_path):
    chain = _chain(tmp_path)
    for i in range(5):
        chain.append("stage_0", "event", {"n": i})
    _tamper_seq(chain, 3)

    assert chain.verify_chain()["valid"] is False
    result = chain.repair()
    assert result["repaired"] is True
    assert result["broken_at_seq"] == 3

    # Chain must verify again after repair
    assert chain.verify_chain()["valid"] is True
    # Repair must be auditable: a chain.repaired event is the tail record
    tail = chain.get_chain()[-1]
    assert tail.component == "chain"
    assert tail.event_type == "repaired"
    assert chain._get_meta("state") == "active"


def test_repair_noop_on_valid_chain(tmp_path):
    chain = _chain(tmp_path)
    chain.append("stage_0", "a", {})
    result = chain.repair()
    assert result["repaired"] is False
    assert chain.verify_chain()["valid"] is True


def test_repair_after_quarantine_full_recovery_loop(tmp_path):
    """The full hygiene h07 loop: tamper -> detect -> quarantine -> repair -> verify."""
    chain = _chain(tmp_path)
    for i in range(5):
        chain.append("stage_0", "event", {"n": i})
    _tamper_seq(chain, 3)

    tamper_detected = chain.verify_chain()["valid"] is False
    quarantined = chain.quarantine()["quarantined"]
    repaired = chain.repair()["repaired"]
    heal_succeeded = chain.verify_chain()["valid"] is True

    assert tamper_detected
    assert quarantined
    assert repaired
    assert heal_succeeded
