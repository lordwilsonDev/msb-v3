"""Merkle proof-of-inclusion for the audit chain (P4).

The audit chain is a hash chain: each record's hash covers its content and
the previous hash, which makes it tamper-evident but gives no *compact*
receipt for one action. Proving one record to a third party today means
handing over the whole prefix and re-hashing it. This module upgrades the
chain to a Certificate-Transparency-style Merkle tree over the record
hashes, so each record gets:

    * a Merkle root (committed in the signed anchor snapshot — a third
      party holding ONLY the anchor + one receipt can verify a single
      action without trusting us or downloading the chain);
    * an inclusion proof (leaf hash + sibling hashes along the path to the
      root) that is O(log n) and independently verifiable.

Tree shape (RFC 6962-style, deterministic and complete):

    * leaves  = H(0x00 || record_hash)   (domain-separated leaf node)
    * parents = H(0x01 || left || right)
    * every level is padded to an even count by duplicating its last node,
      so the tree is complete and a bit-walk verification works: for each
      sibling in the path, hash (node, sibling) when the position is even
      else (sibling, node), halving the position each level. The final
      digest must equal the root.

The 0x00/0x01 prefixes are the standard Merkle domain separation that
prevents a leaf from being presented as an internal node (second-preimage
resistance at the tree level). Nothing here touches the database — this
module is pure functions over record hashes; ``AuditChain.merkle_root()`` /
``inclusion_proof()`` / ``verify_inclusion()`` in audit_chain.py wire it to
the chain.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional

# Root of an empty tree (matches the chain's genesis convention).
EMPTY_ROOT = "0" * 64

_LEAF_PREFIX = b"\x00"
_NODE_PREFIX = b"\x01"


def _leaf_digest(record_hash: str) -> bytes:
    return hashlib.sha256(_LEAF_PREFIX + record_hash.encode()).digest()


def _node_digest(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(_NODE_PREFIX + left + right).digest()


def _build_levels(record_hashes: List[str]) -> List[List[bytes]]:
    """All tree levels, leaf level first, root level last (single node).

    Each level is padded to an even count by duplicating its last node
    (RFC 6962-style), so the tree is complete and bit-walk verification
    needs no tree-shape metadata beyond the leaf index.
    """
    if not record_hashes:
        return []
    level = [_leaf_digest(h) for h in record_hashes]
    # Pad the leaf level to an even count too, so EVERY level in the list is
    # even-length except the root — the sibling walk in inclusion_proof
    # indexes level[idx ^ 1] and must never run past the end.
    if len(level) % 2 == 1:
        level = level + [level[-1]]
    levels = [level]
    while len(level) > 1:
        level = [
            _node_digest(level[i], level[i + 1])
            for i in range(0, len(level), 2)
        ]
        if len(level) % 2 == 1 and len(level) > 1:
            level = level + [level[-1]]
        levels.append(level)
    return levels


def merkle_root(record_hashes: List[str]) -> str:
    """The Merkle root over ``record_hashes`` (in chain order)."""
    levels = _build_levels(record_hashes)
    if not levels:
        return EMPTY_ROOT
    return levels[-1][0].hex()


@dataclass
class InclusionProof:
    """Compact, independently-verifiable receipt for one audit record.

    ``path`` is the sibling hashes from leaf level up to (but not
    including) the root, hex-encoded. Verification needs only this object
    (and optionally an expected root): walk the path with the leaf index's
    bits deciding hash order, and the result must equal ``root``.
    """

    seq: int
    leaf_hash: str
    leaf_index: int
    tree_size: int
    root: str
    path: List[str] = field(default_factory=list)


def inclusion_proof(record_hashes: List[str], leaf_index: int) -> InclusionProof:
    """Build the inclusion proof for the record at ``leaf_index``.

    ``leaf_index`` is the 0-based position in ``record_hashes``. The proof's
    ``seq`` is left to the caller to fill (the chain knows the record's real
    seq; this module works purely in tree coordinates).
    """
    if not record_hashes:
        raise ValueError("cannot build an inclusion proof over an empty chain")
    if not 0 <= leaf_index < len(record_hashes):
        raise IndexError(f"leaf_index {leaf_index} out of range for {len(record_hashes)} records")
    levels = _build_levels(record_hashes)
    path: List[str] = []
    idx = leaf_index
    for level in levels[:-1]:
        sibling = level[idx ^ 1]
        path.append(sibling.hex())
        idx //= 2
    return InclusionProof(
        seq=0,  # caller fills the chain seq
        leaf_hash=record_hashes[leaf_index],
        leaf_index=leaf_index,
        tree_size=len(record_hashes),
        root=levels[-1][0].hex(),
        path=path,
    )


def verify_inclusion(proof: InclusionProof, expected_root: Optional[str] = None) -> bool:
    """Verify ``proof``: recompute the root from leaf + path and compare.

    When ``expected_root`` is given it must also match ``proof.root`` — this
    is how a verifier holding the signed anchor checks a receipt against the
    committed root without trusting the prover's ``root`` field.
    """
    if not 0 <= proof.leaf_index < proof.tree_size:
        return False
    node = _leaf_digest(proof.leaf_hash)
    idx = proof.leaf_index
    for sibling_hex in proof.path:
        try:
            sibling = bytes.fromhex(sibling_hex)
        except ValueError:
            return False
        if idx % 2 == 0:
            node = _node_digest(node, sibling)
        else:
            node = _node_digest(sibling, node)
        idx //= 2
    if node.hex() != proof.root:
        return False
    if expected_root is not None and expected_root != proof.root:
        return False
    return True
