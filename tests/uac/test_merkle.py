"""Merkle proof-of-inclusion (P4) — pure tree + audit-chain integration.

The audit chain's hash chain gives tamper-evidence but no compact receipt
for ONE action. These tests pin the Merkle upgrade: every record gets an
O(log n) inclusion proof that a third party holding only the signed anchor's
committed root can verify independently — the leap from "my ledger" to
"a ledger anyone can verify".
"""
from __future__ import annotations

import pytest

from msb_ledger.audit_chain import AuditChain
from msb_ledger.chain_anchor import ChainAnchor, generate_seed
from msb_ledger.merkle import (
    EMPTY_ROOT,
    inclusion_proof,
    merkle_root,
    verify_inclusion,
)


def _hashes(n: int) -> list[str]:
    return [f"{i:064x}" for i in range(n)]


# ── pure tree ────────────────────────────────────────────────────────────


def test_empty_chain_root_is_genesis() -> None:
    assert merkle_root([]) == EMPTY_ROOT


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6, 7, 8, 9, 16, 17])
def test_every_record_roundtrips(n: int) -> None:
    """Every leaf's proof verifies against the same root, for even and odd
    counts (the RFC 6962-style padding must be transparent to verification)."""
    hashes = _hashes(n)
    root = merkle_root(hashes)
    for i in range(n):
        proof = inclusion_proof(hashes, i)
        assert proof.root == root
        assert proof.tree_size == n
        assert verify_inclusion(proof)
        # A verifier holding the committed root checks the proof against it —
        # the prover's own root field must not be trusted.
        assert verify_inclusion(proof, expected_root=root)


def test_proof_is_compact() -> None:
    """Path length is O(log n): ~log2(next_pow2(n)) siblings, not the whole
    chain — that is the whole point of the receipt."""
    n = 1000
    hashes = _hashes(n)
    proof = inclusion_proof(hashes, 0)
    assert len(proof.path) == 10  # next_pow2(1000)=1024 -> 10 levels
    assert proof.tree_size == n


def test_tampered_leaf_breaks_proof() -> None:
    """Changing one record changes the root, so its own receipt no longer
    verifies — proof-of-inclusion is tamper-evident per action."""
    hashes = _hashes(5)
    proof = inclusion_proof(hashes, 2)
    tampered = list(hashes)
    tampered[2] = "f" * 64
    assert not verify_inclusion(proof, expected_root=merkle_root(tampered))


def test_wrong_expected_root_fails() -> None:
    hashes = _hashes(4)
    proof = inclusion_proof(hashes, 0)
    assert not verify_inclusion(proof, expected_root="f" * 64)


def test_bounds_checked() -> None:
    with pytest.raises(ValueError):
        inclusion_proof([], 0)
    with pytest.raises(IndexError):
        inclusion_proof(_hashes(3), 3)


def test_malformed_path_rejected() -> None:
    hashes = _hashes(4)
    proof = inclusion_proof(hashes, 1)
    proof.path[0] = "not-hex"
    assert not verify_inclusion(proof)


# ── audit-chain integration ──────────────────────────────────────────────


def _chain(tmp_path) -> AuditChain:
    return AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)


def test_chain_merkle_root_covers_all_records(tmp_path) -> None:
    chain = _chain(tmp_path)
    assert chain.merkle_root() == EMPTY_ROOT
    for i in range(7):
        chain.append("test", "event", {"n": i})
    # Root must change as records are appended (each new tip extends the tree).
    root_before = chain.merkle_root()
    chain.append("test", "event", {"n": 7})
    assert chain.merkle_root() != root_before
    # And the chain's own root must match the pure function over its hashes.
    assert chain.merkle_root() == merkle_root(
        [r.record_hash for r in chain.get_chain()]
    )


def test_inclusion_proof_by_seq(tmp_path) -> None:
    chain = _chain(tmp_path)
    for i in range(5):
        chain.append("test", "event", {"n": i})
    proof = chain.inclusion_proof(seq=3)
    assert proof.seq == 3
    # seq is 1-based; the record at seq 3 is the 3rd appended (index 2).
    assert proof.leaf_hash == chain.get_chain()[2].record_hash
    assert proof.leaf_index == 2
    assert chain.verify_inclusion(proof)
    # A content-only payload edit is caught by verify_chain (stored hash != re-
    # computed). The Merkle receipt's job is the harder case: a HASH-CONSISTENT
    # rewrite that keeps the linear chain internally valid but changes the
    # committed root — the receipt must break.
    import json
    import sqlite3

    from msb_ledger.audit_chain import _compute_hash, _create_triggers, _drop_triggers

    with sqlite3.connect(chain.db_path) as conn:
        conn.row_factory = sqlite3.Row
        _drop_triggers(conn)
        rows = conn.execute(
            "SELECT * FROM audit_records ORDER BY seq ASC").fetchall()
        prev = "0" * 64
        for row in rows:
            payload = json.loads(row["payload"])
            if row["seq"] == 3:
                payload = {"n": 999}  # the tampered content
            new_hash = _compute_hash(
                prev, row["component"], row["event_type"], payload, row["timestamp"]
            )
            conn.execute(
                "UPDATE audit_records SET payload=?, prev_hash=?, record_hash=? WHERE seq=?",
                (json.dumps(payload, ensure_ascii=False), prev, new_hash, row["seq"]),
            )
            prev = new_hash
        _create_triggers(conn)
    # The linear chain is internally valid again (attacker recomputed hashes)...
    assert chain.verify_chain()["valid"] is True
    # ...but the Merkle root changed, so the old receipt no longer verifies.
    assert chain.merkle_root() != proof.root
    assert not chain.verify_inclusion(proof)


def test_inclusion_proof_unknown_seq(tmp_path) -> None:
    chain = _chain(tmp_path)
    chain.append("test", "event", {"n": 0})
    with pytest.raises(IndexError):
        chain.inclusion_proof(seq=99)


def test_anchor_commits_merkle_root_and_verify_cross_checks(tmp_path) -> None:
    """The signed anchor snapshot commits the Merkle root; a third party
    with only the anchor can verify one receipt, and verify_anchored rejects
    a chain whose content drifted under the anchor."""
    chain = _chain(tmp_path)
    for i in range(6):
        chain.append("test", "event", {"n": i})
    anchor = ChainAnchor(seed=generate_seed(), anchor_path=tmp_path / "chain_anchor.json")
    result = anchor.anchor(chain)
    committed_root = result["snapshot"]["merkle_root"]
    assert committed_root == chain.merkle_root()

    # The committed root verifies a single receipt without the chain.
    proof = chain.inclusion_proof(seq=4)
    assert verify_inclusion(proof, expected_root=committed_root)
    # Receipts for the record that will be tampered (seq 2) AND one that will
    # be affected by the rewrite (seq 4 — every hash after the break changes
    # in a hash chain).
    proof_seq1 = chain.inclusion_proof(seq=1)
    proof_seq2 = chain.inclusion_proof(seq=2)
    assert chain.verify_inclusion(proof_seq1, expected_root=committed_root)
    assert chain.verify_inclusion(proof_seq2, expected_root=committed_root)
    assert chain.verify_inclusion(proof, expected_root=committed_root)

    # verify: valid when the chain matches the anchor.
    anchored = anchor.verify(chain)
    assert anchored["valid"] is True, anchored

    # A HASH-CONSISTENT rewrite (attacker recomputes every hash, so the
    # linear chain is internally valid again — see the by-seq test for the
    # full rewrite helper) changes the committed Merkle root. The third-party
    # receipt check fails against the anchor's committed root, and
    # anchor.verify() rejects the drift via the merkle-root cross-check.
    import json
    import sqlite3

    from msb_ledger.audit_chain import _compute_hash, _create_triggers, _drop_triggers

    with sqlite3.connect(chain.db_path) as conn:
        conn.row_factory = sqlite3.Row
        _drop_triggers(conn)
        rows = conn.execute("SELECT * FROM audit_records ORDER BY seq ASC").fetchall()
        prev = "0" * 64
        for row in rows:
            payload = json.loads(row["payload"])
            if row["seq"] == 2:
                payload = {"n": 999}  # tampered content
            new_hash = _compute_hash(
                prev, row["component"], row["event_type"], payload, row["timestamp"]
            )
            conn.execute(
                "UPDATE audit_records SET payload=?, prev_hash=?, record_hash=? WHERE seq=?",
                (json.dumps(payload, ensure_ascii=False), prev, new_hash, row["seq"]),
            )
            prev = new_hash
        _create_triggers(conn)
    assert chain.verify_chain()["valid"] is True  # linear chain still valid
    # The record BEFORE the tamper point (seq 1) keeps its receipt — its hash
    # is unchanged (prev = genesis, content untouched)...
    assert chain.verify_inclusion(proof_seq1, expected_root=committed_root)
    # ...but the tampered record (seq 2) and every record after it broke: the
    # chain-level check binds each receipt to its LIVE record hash, which the
    # hash-consistent rewrite changed. The anchor rejects the rewritten chain
    # via the merkle-root cross-check.
    assert not chain.verify_inclusion(proof_seq2, expected_root=committed_root)
    assert not chain.verify_inclusion(proof, expected_root=committed_root)
    # The anchor rejects the rewritten chain. The tip mismatch (first gate)
    # fires here because a hash-consistent rewrite moves the tip; the
    # merkle-root cross-check is the redundant second commitment that would
    # catch a rewrite able to defeat the linear chain_sha256. Either way the
    # rewrite is refused.
    check = anchor.verify(chain)
    assert check["valid"] is False


def test_cli_receipt_roundtrip(tmp_path) -> None:
    """The CLI emits a receipt and verifies it back — the operator path for
    handing a compact, independently-verifiable proof of one action to a
    third party."""
    import json
    import subprocess

    chain = _chain(tmp_path)
    for i in range(5):
        chain.append("test", "event", {"n": i})

    out = subprocess.run(
        ["python3", "-m", "msb_ledger.chain_anchor", "--receipt", str(chain.db_path),
         "--seq", "3"],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    assert data["valid_against_chain"] is True
    assert data["receipt"]["seq"] == 3
    assert data["receipt"]["tree_size"] == 5

    receipt_file = tmp_path / "receipt.json"
    receipt_file.write_text(json.dumps(data))
    verify = subprocess.run(
        ["python3", "-m", "msb_ledger.chain_anchor", "--verify-receipt",
         str(chain.db_path), "--receipt-file", str(receipt_file)],
        capture_output=True, text=True,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert json.loads(verify.stdout)["valid"] is True

    # Missing --seq is a CLI error, not a silent default.
    bad = subprocess.run(
        ["python3", "-m", "msb_ledger.chain_anchor", "--receipt", str(chain.db_path)],
        capture_output=True, text=True,
    )
    assert bad.returncode != 0


def test_pre_merkle_anchor_still_verifies(tmp_path) -> None:
    """Backward compatibility: an anchor snapshot WITHOUT merkle_root (written
    before the P4 upgrade) must verify unchanged — the field is optional on
    read and only cross-checked when present."""
    import json

    chain = _chain(tmp_path)
    chain.append("test", "event", {"n": 0})
    anchor = ChainAnchor(seed=generate_seed(), anchor_path=tmp_path / "chain_anchor.json")
    snapshot = anchor._snapshot(chain)
    del snapshot["merkle_root"]
    signature = anchor._sign(snapshot)
    # Same record shape as anchor() writes, minus the merkle_root field.
    (tmp_path / "chain_anchor.json").write_text(
        json.dumps(
            {"snapshot": snapshot,
             "signature": signature.hex(),
             "public_key": anchor._pub.hex(),
             "key_algorithm": anchor._algorithm},
            indent=2, sort_keys=True,
        )
    )
    check = anchor.verify(chain)
    assert check["valid"] is True, check
