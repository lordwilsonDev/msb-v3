# Merkle receipts — proof-of-inclusion for audit records (P4)

The audit chain is a hash chain: tamper-evident, but proving ONE action to a
third party meant handing over the whole prefix and re-hashing it. With
Merkle proof-of-inclusion, every audit record gets a compact, independently
verifiable receipt — the leap from "my ledger" to "a ledger anyone can
verify" (Certificate-Transparency / Sigstore-Rekor style).

## What a receipt is

For any record seq, the chain emits:

```json
{
  "receipt": {
    "seq": 3,
    "leaf_hash": "<sha256 of the record's chain hash>",
    "leaf_index": 2,
    "tree_size": 5,
    "root": "<Merkle root over all record hashes>",
    "path": ["<sibling hash>", "<sibling hash>", "..."]
  },
  "committed_merkle_root": "<root committed in the signed anchor, if any>",
  "valid_against_chain": true
}
```

* The **root** is committed inside the signed chain-anchor snapshot, so a
  verifier holding ONLY the anchor file (or a notarized entry) + this
  receipt can check one action without the chain or us.
* The **path** is O(log n) sibling hashes — a few dozen strings even for a
  large chain, not the whole history.
* Verification is a bit-walk: start at the leaf, hash with each sibling
  (left/right decided by the leaf index's bits), and the result must equal
  the root. Pure, no trusted infrastructure.

## CLI

```bash
# Emit a receipt for record seq 3:
python3 -m msb_ledger.chain_anchor --receipt <audit.db> --seq 3 > receipt.json

# Verify it later (exit 0 = valid):
python3 -m msb_ledger.chain_anchor --verify-receipt <audit.db> --receipt-file receipt.json
```

The receipt's `committed_merkle_root` is read from the signed anchor next to
the DB when present, so verification checks against the *externally
committed* root, not just the chain's self-reported one.

## Security model

* **Hash-consistent rewrite (the hard case):** an attacker who rewrites a
  record AND recomputes every hash so the linear chain is internally valid
  again changes the Merkle root. Receipts for the tampered record (and
  every record after it — a hash chain moves all subsequent hashes) stop
  verifying against the committed root, and `verify()` rejects the drift.
  Records BEFORE the tamper point keep valid receipts.
* **Content-only edit:** changing a payload without recomputing hashes is
  caught by the linear chain's own `verify_chain()` (stored hash !=
  recomputed) and by the receipt binding (the record's live hash no longer
  equals the receipt's `leaf_hash`).
* The `0x00`/`0x01` prefixes are the standard Merkle domain separation that
  prevents a leaf from being presented as an internal node.
* Pre-Merkle anchors (no `merkle_root` field) verify unchanged — the field
  is optional on read and only cross-checked when present.

## Library API

```python
from msb_ledger.audit_chain import AuditChain
from msb_ledger.merkle import InclusionProof, verify_inclusion

chain = AuditChain(db_path="...")
root = chain.merkle_root()                 # one root over the whole chain
proof = chain.inclusion_proof(seq=3)       # the receipt for one record
ok = chain.verify_inclusion(proof)         # binds receipt to the live record
ok = verify_inclusion(proof, expected_root=root)  # pure tree check
```

Tests: `tests/uac/test_merkle.py` (pure tree roundtrips n=1..17, path
compactness, tamper cases, anchor cross-check, CLI roundtrip, backward
compat).
