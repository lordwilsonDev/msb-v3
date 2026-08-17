"""Tests for uac.audit_chain.AuditChain — must actually detect tampering,
not just accept/return data, since that's the entire point of the module."""
from __future__ import annotations

import json
import sqlite3

import pytest

from msb_v3.uac.audit_chain import _GENESIS_HASH, AuditChain, tamper


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

    # Simulate someone editing the DB file directly. A raw UPDATE is refused
    # by the append-only trigger, so the edit goes through the helper that
    # defeats the trigger the way a knowledgeable attacker would.
    tamper(chain.db_path, "UPDATE audit_records SET payload=? WHERE seq=1", ('{"amount": 999999}',))

    result = chain.verify_chain()
    assert result["valid"] is False
    assert result["broken_at_seq"] == 1


def test_verify_chain_detects_broken_prev_hash_link(tmp_path):
    chain = _chain(tmp_path)
    chain.append("stage_0", "a", {})
    chain.append("stage_0", "b", {})

    tamper(chain.db_path, "UPDATE audit_records SET prev_hash=? WHERE seq=2", ("deadbeef" * 8,))

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
    tamper(
        chain.db_path,
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


# --- security hardening: append-only triggers + hardened repair ----------------


def test_append_only_triggers_block_raw_mutation(tmp_path):
    chain = _chain(tmp_path)
    chain.append("stage_0", "a", {"x": 1})
    chain.append("stage_0", "b", {"x": 2})

    with sqlite3.connect(chain.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE audit_records SET payload=? WHERE seq=1", ('{"x": 9}',))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit_records WHERE seq=2")
    # The refused edits never landed: the chain still verifies.
    assert chain.verify_chain()["valid"] is True
    assert chain.get_chain()[0].payload == {"x": 1}


def test_append_rejects_non_finite_float(tmp_path):
    chain = _chain(tmp_path)
    with pytest.raises(ValueError, match="non-finite float"):
        chain.append("stage_0", "event", {"score": float("nan")})
    with pytest.raises(ValueError, match="non-finite float"):
        chain.append("stage_0", "event", {"nested": {"x": float("inf")}})
    # Finite floats + unicode round-trip to the same hash (no false break).
    chain.append("stage_0", "event", {"score": 0.95, "note": "héllo ✓"})
    assert chain.verify_chain()["valid"] is True


def test_repair_requires_operator_when_configured(tmp_path, monkeypatch):
    # The ledger is standalone (P4 extraction): its operator token lives on
    # msb_ledger.config.settings, not the host app's config.
    from msb_ledger.config import settings

    monkeypatch.setattr(settings, "operator_token", "sekret")
    chain = _chain(tmp_path)
    for i in range(3):
        chain.append("stage_0", "event", {"n": i})
    _tamper_seq(chain, 2)

    with pytest.raises(PermissionError, match="operator token"):
        chain.repair()
    # The chain is still broken — the refused repair did not heal it.
    assert chain.verify_chain()["valid"] is False
    assert chain.repair(operator="sekret")["repaired"] is True


def _anchor(tmp_path):
    from msb_v3.uac.chain_anchor import ChainAnchor, generate_seed

    return ChainAnchor(seed=generate_seed(), anchor_path=tmp_path / "chain_anchor.json")


def test_repair_refuses_when_notary_tip_absent(tmp_path):
    chain = _chain(tmp_path)
    anchor = _anchor(tmp_path)
    notary = tmp_path / "notary.jsonl"
    for i in range(5):
        chain.append("stage_0", "event", {"n": i})
    anchor.anchor(chain)
    anchor.notarize(chain, notary)

    # Whole-DB rollback + a break: the notarized tail record is gone and the
    # chain is internally broken. repair() must refuse, not launder it.
    tamper(chain.db_path, "DELETE FROM audit_records WHERE seq = 5")
    _tamper_seq(chain, 3)
    assert chain.verify_chain()["valid"] is False

    with pytest.raises(PermissionError, match="notary"):
        chain.repair(operator="sekret", anchor=anchor, notary_log=notary)


def test_repair_accepts_and_renotarizes_when_notary_corroborates(tmp_path):
    chain = _chain(tmp_path)
    anchor = _anchor(tmp_path)
    notary = tmp_path / "notary.jsonl"
    for i in range(5):
        chain.append("stage_0", "event", {"n": i})
    anchor.anchor(chain)
    anchor.notarize(chain, notary)
    assert len(notary.read_text().splitlines()) == 1

    _tamper_seq(chain, 3)
    result = chain.repair(anchor=anchor, notary_log=notary)
    assert result["repaired"] is True
    assert result["notarized"] is True
    assert chain.verify_chain()["valid"] is True
    assert anchor.verify(chain)["valid"] is True
    # Forced re-notarization appended a fresh snapshot.
    assert len(notary.read_text().splitlines()) == 2


# --- verify-before-trust (security-hardening #3) -----------------------------


def test_verify_trustworthy_passes_on_intact_chain_and_fails_on_tamper(tmp_path):
    from msb_v3.uac.audit_chain import verify_trustworthy

    chain = _chain(tmp_path)
    chain.append("stage_0", "a", {"x": 1})
    assert verify_trustworthy(chain)["valid"] is True

    _tamper_seq(chain, 1)
    assert verify_trustworthy(chain)["valid"] is False


def test_verify_trustworthy_honors_external_anchor(tmp_path):
    from msb_v3.uac.audit_chain import verify_trustworthy

    class _Anchored:
        def verify_chain(self):
            return {"valid": True}

        def verify_anchored(self):
            return {"valid": False, "reason": "whole-DB rollback detected"}

    result = verify_trustworthy(_Anchored())
    assert result["valid"] is False
    assert result["reason"] == "whole-DB rollback detected"


# --- RFC 3161 timestamp proofs on notary entries (security-hardening #9) -----


def _stamped_entry(anchor, chain, *, source="receive_time", verified=False):
    from msb_v3.uac.timestamping import LocalReceiveTimestamper, TimestampProof

    entry = anchor.build_notary_entry(chain)
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
    proof = LocalReceiveTimestamper().stamp(canonical)
    if source == "rfc3161":
        proof = TimestampProof(
            source="rfc3161",
            digest_sha256=proof.digest_sha256,
            tsa_url="https://tsa.invalid",
            gen_time="2026-08-16T07:10:05+00:00",
            token_b64="dG9rZW4=",
            verified=verified,
            note="synthetic",
        )
    entry["timestamp"] = proof.to_dict()
    return entry


def test_verify_notary_accepts_covering_receive_time_proof(tmp_path):
    chain = _chain(tmp_path)
    chain.append("stage_0", "e", {"n": 1})
    anchor = _anchor(tmp_path)
    entry = _stamped_entry(anchor, chain)

    result = anchor.verify_notary_entry(entry, chain)
    assert result["valid"] is True
    assert result["timestamp_valid"] is True
    assert result["timestamp_source"] == "receive_time"


def test_verify_notary_rejects_timestamp_not_covering_entry(tmp_path):
    chain = _chain(tmp_path)
    chain.append("stage_0", "e", {"n": 1})
    anchor = _anchor(tmp_path)
    entry = _stamped_entry(anchor, chain)
    # Swap in a proof stamped over a DIFFERENT entry (different notarized_at):
    # the signature is untouched, but the proof no longer covers this entry.
    other = anchor.build_notary_entry(chain)
    other_canonical = json.dumps(other, sort_keys=True, separators=(",", ":")).encode()
    entry["timestamp"] = {
        "source": "receive_time",
        "digest_sha256": __import__("hashlib").sha256(other_canonical).hexdigest(),
        "received_at": "2026-08-16T07:10:06+00:00",
        "verified": False,
        "note": "",
    }

    result = anchor.verify_notary_entry(entry, chain)
    assert result["valid"] is False
    assert "does not cover" in result["reason"]


def test_verify_notary_rejects_unverified_rfc3161_proof(tmp_path):
    chain = _chain(tmp_path)
    chain.append("stage_0", "e", {"n": 1})
    anchor = _anchor(tmp_path)
    entry = _stamped_entry(anchor, chain, source="rfc3161", verified=False)

    result = anchor.verify_notary_entry(entry, chain)
    assert result["valid"] is False
    assert "not verified" in result["reason"]


def test_verify_notary_reports_verified_rfc3161_proof(tmp_path):
    chain = _chain(tmp_path)
    chain.append("stage_0", "e", {"n": 1})
    anchor = _anchor(tmp_path)
    entry = _stamped_entry(anchor, chain, source="rfc3161", verified=True)

    result = anchor.verify_notary_entry(entry, chain)
    assert result["valid"] is True
    assert result["timestamp_valid"] is True
    assert result["timestamp_source"] == "rfc3161"
    assert result["timestamp_gen_time"] == "2026-08-16T07:10:05+00:00"
