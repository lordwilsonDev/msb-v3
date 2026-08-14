"""External chain-tip anchor — closes the T7 gap (whole audit-DB replacement).

A hash chain proves non-modification of the records you still have; it cannot
prove you still have the right records (MSB-GOV-EVAL-001 §13, T7). If an
attacker replaces the whole audit DB with an older, internally-valid snapshot,
``AuditChain.verify_chain()`` stays green.

The fix is an EXTERNAL anchor: a signed snapshot of the chain tip stored in a
separate file (and optionally notarized out-of-band). ``verify_anchored()``
recomputes the live chain tip and compares it against the anchored tip; any
replacement of the DB changes the tip and is DETECTED — unless the attacker
also holds the anchor signing key, which is the documented trust boundary of
any external anchor (the manifest's §exclusions already classify this
correctly: the anchor moves whole-DB replacement from "undetectable" to
"detectable unless the signing key is compromised").

Trust model:
  - anchor key  : Ed25519 seed, held outside the audit DB (env or keyfile).
                  The key owner signs snapshots; the DB alone cannot forge one.
  - anchor file : ``chain_anchor.json`` next to the audit DB — a separate file,
                  so replacing just the DB leaves the old anchor behind.
  - notarize    : exports the signed snapshot to a location the attacker cannot
                  reach (backup, remote), so the anchor cannot be rolled back
                  together with the DB.

Anchoring is per-append in the ``AnchoredAuditChain`` wrapper: every append
commits the record, then re-signs the new tip. A stale anchor (records appended
after the last anchor) is itself detectable by verification — the operator must
re-anchor after legitimate appends, which the wrapper does automatically.

Key sources (fail-closed): ``MSB_CHAIN_ANCHOR_KEY`` (64 hex chars) or the
keyfile at ``<msb_home>/data/uac/chain_anchor_key``. If a key is configured but
unreadable, construction RAISES — anchoring requested but unavailable must
never degrade silently to an unsigned chain.

CLI:
    python -m msb_v3.uac.chain_anchor --verify <audit.db> [--anchor <audit.db>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from msb_v3.core.config import settings
from msb_v3.uac.audit_chain import AuditChain

ANCHOR_FILENAME = "chain_anchor.json"
KEY_ENV = "MSB_CHAIN_ANCHOR_KEY"
_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_key_path() -> Path:
    return Path(settings.msb_home) / "data" / "uac" / "chain_anchor_key"


def _default_anchor_path(db_path: Path) -> Path:
    return Path(db_path).with_name(ANCHOR_FILENAME)


def generate_seed() -> bytes:
    """Return a fresh 32-byte Ed25519 private seed."""
    return Ed25519PrivateKey.generate().private_bytes_raw()


def _private_key(seed: bytes) -> Ed25519PrivateKey:
    if len(seed) != 32:
        raise ValueError("chain anchor key must be 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(seed)


class ChainAnchor:
    """Signs and verifies external chain-tip snapshots for an AuditChain."""

    def __init__(
        self,
        seed: Optional[bytes] = None,
        anchor_path: Optional[str | Path] = None,
        public_key: Optional[bytes] = None,
    ) -> None:
        """Either a private ``seed`` (signing + verifying) or a ``public_key``
        (verify-only, e.g. a notarized copy on a machine without the key)."""
        self._signing = seed is not None
        if seed is not None:
            self._priv = _private_key(seed)
            self._pub = self._priv.public_key().public_bytes_raw()
        elif public_key is not None:
            if len(public_key) != 32:
                raise ValueError("public key must be 32 bytes")
            self._pub = public_key
        else:
            raise ValueError("ChainAnchor requires a seed or a public key")
        self.anchor_path = Path(anchor_path) if anchor_path else None

    @classmethod
    def from_env(cls) -> "ChainAnchor":
        """Load the key from MSB_CHAIN_ANCHOR_KEY or the keyfile. Fail-closed:
        a configured-but-unreadable key raises rather than degrading silently."""
        raw = os.getenv(KEY_ENV)
        if raw is None:
            keyfile = _default_key_path()
            if keyfile.exists():
                raw = keyfile.read_text().strip()
            else:
                raise ValueError(
                    f"no chain anchor key configured: set {KEY_ENV} or create {keyfile}"
                )
        try:
            seed = bytes.fromhex(raw.strip())
        except ValueError as exc:
            raise ValueError(f"{KEY_ENV} must be 64 hex chars") from exc
        return cls(seed=seed)

    # ── Snapshot ──────────────────────────────────────────────────────────────
    def _snapshot(self, chain: AuditChain) -> Dict[str, Any]:
        records = chain.get_chain()
        if records:
            tip_hash = records[-1].record_hash
            seq = records[-1].seq
        else:
            tip_hash = "0" * 64
            seq = 0
        chain_sha = hashlib.sha256(
            "\n".join(r.record_hash for r in records).encode()
        ).hexdigest()
        return {
            "version": _VERSION,
            "db_path": str(Path(chain.db_path).resolve()),
            "seq": seq,
            "record_count": len(records),
            "tip_hash": tip_hash,
            "chain_sha256": chain_sha,
            "anchored_at": _now_iso(),
        }

    def _sign(self, snapshot: Dict[str, Any]) -> bytes:
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        return self._priv.sign(canonical)

    def _verify_signature(self, snapshot: Dict[str, Any], signature: bytes) -> bool:
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        try:
            Ed25519PublicKey.from_public_bytes(self._pub).verify(signature, canonical)
            return True
        except InvalidSignature:
            return False

    # ── Anchor store ──────────────────────────────────────────────────────────
    def _store_path(self, chain: AuditChain) -> Path:
        return self.anchor_path or _default_anchor_path(Path(chain.db_path))

    def anchor(self, chain: AuditChain) -> Dict[str, Any]:
        """Sign the current chain tip and persist the snapshot + signature."""
        if not self._signing:
            raise ValueError("verify-only anchor cannot sign new snapshots")
        snapshot = self._snapshot(chain)
        signature = self._sign(snapshot)
        record = {
            "snapshot": snapshot,
            "signature": signature.hex(),
            "public_key": self._pub.hex(),
        }
        path = self._store_path(chain)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.urandom(4).hex()}.tmp")
        try:
            tmp.write_text(json.dumps(record, indent=2, sort_keys=True))
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return record

    def _read_anchor(self, chain: AuditChain) -> Optional[Dict[str, Any]]:
        path = self._store_path(chain)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    # ── Verification ──────────────────────────────────────────────────────────
    def verify(self, chain: AuditChain) -> Dict[str, Any]:
        """Check (1) an anchor exists, (2) its signature is valid, (3) the live
        chain tip matches the anchored tip. Returns the same shape as
        ``AuditChain.verify_chain()`` so callers can treat both uniformly."""
        anchor = self._read_anchor(chain)
        if anchor is None:
            return {"valid": False, "reason": "no external chain anchor exists",
                    "note": "T7 (whole-DB replacement) is undetectable without an anchor"}
        snapshot = anchor["snapshot"]
        signature = bytes.fromhex(anchor["signature"])
        pub_matches = bytes.fromhex(anchor["public_key"]) == self._pub
        if not pub_matches:
            return {"valid": False, "reason": "anchor public key does not match the verifying key",
                    "anchored_tip": snapshot.get("tip_hash")}
        if not self._verify_signature(snapshot, signature):
            return {"valid": False, "reason": "anchor signature invalid — anchor file tampered",
                    "anchored_tip": snapshot.get("tip_hash")}
        live = self._snapshot(chain)
        if live["tip_hash"] != snapshot["tip_hash"] or live["seq"] != snapshot["seq"]:
            return {
                "valid": False,
                "reason": "chain tip does not match external anchor — "
                          "whole-DB replacement or rollback detected (T7)",
                "anchored_tip": snapshot["tip_hash"],
                "anchored_seq": snapshot["seq"],
                "live_tip": live["tip_hash"],
                "live_seq": live["seq"],
            }
        if live["chain_sha256"] != snapshot["chain_sha256"]:
            return {"valid": False, "reason": "chain fingerprint mismatch",
                    "anchored_chain_sha256": snapshot["chain_sha256"],
                    "live_chain_sha256": live["chain_sha256"]}
        return {"valid": True, "record_count": live["record_count"],
                "tip_hash": live["tip_hash"], "anchored_at": snapshot["anchored_at"]}

    def notarize(self, chain: AuditChain, dest: str | Path, *, append: bool = True) -> Path:
        """Export the current signed anchor out-of-band (append-only log)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        anchor = self._read_anchor(chain)
        if anchor is None:
            self.anchor(chain)
            anchor = self._read_anchor(chain)
        entry = {"notarized_at": _now_iso(), "anchor": anchor}
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        if append and dest.exists():
            with dest.open("a") as handle:
                handle.write(line + "\n")
        else:
            # bare anchor copy — directly usable as an anchor store
            dest.write_text(json.dumps(anchor, indent=2, sort_keys=True) + "\n")
        return dest


class AnchoredAuditChain:
    """AuditChain wrapper that re-anchors after every append, so the external
    anchor never goes stale. Duck-types the AuditChain surface used by the
    vesta services (append / verify_chain / get_chain)."""

    def __init__(self, chain: AuditChain, anchor: ChainAnchor) -> None:
        self.chain = chain
        self.anchor = anchor
        # establish the initial anchor so verification is meaningful from birth
        self.anchor.anchor(chain)

    @property
    def db_path(self) -> Path:
        return self.chain.db_path

    def append(self, component: str, event_type: str, payload: Dict[str, Any]) -> Any:
        record = self.chain.append(component, event_type, payload)
        self.anchor.anchor(self.chain)
        return record

    def verify_chain(self) -> Dict[str, Any]:
        return self.chain.verify_chain()

    def verify_anchored(self) -> Dict[str, Any]:
        return self.anchor.verify(self.chain)

    def get_chain(self, component: Optional[str] = None) -> list:
        return self.chain.get_chain(component=component)


def anchored_chain_from_env() -> AuditChain | AnchoredAuditChain:
    """Factory used at service wiring sites: anchored when a key is configured,
    plain AuditChain otherwise (zero behavior change without a key)."""
    raw = os.getenv(KEY_ENV)
    if raw is None and not _default_key_path().exists():
        return AuditChain()
    return AnchoredAuditChain(AuditChain(), ChainAnchor.from_env())


def _main() -> int:
    parser = argparse.ArgumentParser(description="External chain-tip anchor (T7 fix)")
    parser.add_argument("--verify", metavar="AUDIT_DB", help="verify the chain against its external anchor")
    parser.add_argument("--anchor", metavar="AUDIT_DB", help="sign and persist a fresh anchor for the chain")
    parser.add_argument("--notarize", metavar="DEST", help="export the signed anchor out-of-band")
    args = parser.parse_args()
    if args.verify:
        chain = AuditChain(args.verify)
        anchor = ChainAnchor.from_env()
        report = {"internal_chain": chain.verify_chain(), "anchored": anchor.verify(chain)}
        print(json.dumps(report, indent=2))
        return 0 if report["anchored"]["valid"] else 1
    if args.anchor:
        chain = AuditChain(args.anchor)
        record = ChainAnchor.from_env().anchor(chain)
        print(json.dumps({"anchored": record["snapshot"]}, indent=2))
        return 0
    parser.error("specify --verify or --anchor")


if __name__ == "__main__":
    raise SystemExit(_main())
