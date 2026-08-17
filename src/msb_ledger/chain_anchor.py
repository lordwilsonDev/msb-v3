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

Key rotation / recovery (the ``chain_key_registry.json``) — a hardware move
creates a recovery problem: if the enclave/YubiKey dies, the whole history
becomes unverifiable unless a successor or offline recovery key was prepared.
The registry (a second file next to the anchor) records:

  * ``current_public_key`` — the key that signs new anchors today.
  * ``recovery_public_key`` — an offline recovery key (public half only is
    stored; the seed is kept off-box by the operator).
  * ``rotations`` — cross-signed successor endorsements: the OLD key signs a
    statement endorsing the NEW key, so a successor is provably authorized by
    its predecessor (``from_signature``).
  * ``revocations`` — signed records that a key is retired; a revoked key is
    no longer accepted for NEW anchors, but its historical notary entries
    remain verifiable (a revoked key cannot un-sign history).

Verification accepts an anchor signed by the current key OR a registered
recovery key; ``verify_notary_entry`` accepts any key ever registered
(current, recovery, or a cross-signed successor) so the pre-rotation history
stays verifiable after a hardware move. Operator actions: ``--rotate``,
``--register-recovery``, ``--revoke``, ``--recover``.

CLI:
    python -m msb_ledger.chain_anchor --verify <audit.db> [--anchor <audit.db>]
    python -m msb_ledger.chain_anchor --notarize <audit.db> --notary <log>
    python -m msb_ledger.chain_anchor --verify-notary <audit.db> --notary <log>
    python -m msb_ledger.chain_anchor --rotate <audit.db> --backend <name> \
        --reason <why>          # cross-signed successor: old key endorses new
    python -m msb_ledger.chain_anchor --register-recovery <audit.db> \
        --recovery-public-key <hex> [--algorithm <alg>]
    python -m msb_ledger.chain_anchor --revoke <audit.db> --reason <why>
    python -m msb_ledger.chain_anchor --recover <audit.db> --seed <hex> \
        --reason <why>          # re-anchor with the registered recovery key
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from msb_ledger.audit_chain import AuditChain
from msb_ledger.config import settings
from msb_ledger.signing import (
    ED25519,
    SigningBackend,
    SoftwareEd25519Backend,
    build_backend,
    verify_signature,
)
from msb_ledger.timestamping import TimestampProof

ANCHOR_FILENAME = "chain_anchor.json"
REGISTRY_FILENAME = "chain_key_registry.json"
KEY_ENV = "MSB_CHAIN_ANCHOR_KEY"
BACKEND_ENV = "MSB_CHAIN_ANCHOR_BACKEND"
_VERSION = 1
REGISTRY_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chain_newer_than_anchor(chain: AuditChain, anchored_at_iso: str) -> float:
    """Seconds the chain's newest record is newer than the signed anchor.
    Returns 0 when the anchor covers the whole chain (healthy)."""
    records = chain.get_chain()
    if not records:
        return 0.0
    try:
        anchored_at = datetime.fromisoformat(anchored_at_iso.replace("Z", "+00:00"))
        newest = datetime.fromisoformat(records[-1].timestamp.replace("Z", "+00:00"))
    except ValueError:
        return 0.0  # unparseable timestamps — treat as covered, not stale
    return max(0.0, (newest - anchored_at).total_seconds())


def _default_key_path() -> Path:
    return Path(settings.msb_home) / "data" / "uac" / "chain_anchor_key"


def _seed_from_keychain() -> Optional[str]:
    """Resolve the anchor seed from the macOS login keychain (a generic
    password item) so the key need not live in a plaintext file. Gated on
    MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE (set by scripts/store-anchor-key.sh):
    unset => never invoked, zero behavior change and no subprocess. Returns
    None when the item is absent or `security` is unavailable; the caller
    reports the missing key fail-closed."""
    service = os.getenv("MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE", "").strip()
    if not service:
        return None
    account = os.getenv("MSB_CHAIN_ANCHOR_KEYCHAIN_ACCOUNT", "msb-v3")
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None  # no `security` CLI (non-macOS) — caller fails closed
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _default_anchor_path(db_path: Path) -> Path:
    return Path(db_path).with_name(ANCHOR_FILENAME)


def generate_seed() -> bytes:
    """Return a fresh 32-byte Ed25519 private seed."""
    return Ed25519PrivateKey.generate().private_bytes_raw()


def _default_registry_path(db_path: Path) -> Path:
    return Path(db_path).with_name(REGISTRY_FILENAME)


class KeyRegistry:
    """Cross-signed key-rotation + recovery records for the chain anchor.

    A second file (``chain_key_registry.json``) next to the anchor records the
    chain of custody: which key is current, which offline recovery key is
    authorized, which successor endorsements were cross-signed by their
    predecessor, and which keys have been revoked.

    The registry is only as trustworthy as the anchor file itself (both sit
    next to the audit DB and both are covered by the off-box notary), but it
    gives verification the vocabulary to answer "is this key authorized to
    sign new anchors?" after a rotation — without it, a hardware move leaves
    every pre-rotation notary entry unverifiable.

    Records:
      rotations: [{from, to, rotated_at, reason, from_signature}]
                 `from` (the OLD key) signs a statement endorsing `to` (the
                 NEW key). ``from_signature`` is over the canonical rotation
                 body (everything but the signature field).
      revocations: [{key, revoked_at, reason, signature}]
                 the current key signs that `key` is retired. A revoked key is
                 not accepted for NEW anchors, but its historical notary
                 entries stay verifiable.
    """

    def __init__(self, current_public_key: bytes, algorithm: str = ED25519,
                 registry_path: Optional[str | Path] = None) -> None:
        self.current_public_key = current_public_key
        self.algorithm = algorithm
        self.registry_path = Path(registry_path) if registry_path else None
        self.recovery_public_key: Optional[bytes] = None
        self.recovery_algorithm: Optional[str] = None
        self.rotations: list[Dict[str, Any]] = []
        self.revocations: list[Dict[str, Any]] = []

    @classmethod
    def load(cls, chain: AuditChain, registry_path: Optional[str | Path] = None) -> "KeyRegistry":
        """Load an existing registry, or create an empty one (current key
        unknown until an anchor exists / is created)."""
        path = Path(registry_path) if registry_path else _default_registry_path(Path(chain.db_path))
        if path.exists():
            data = json.loads(path.read_text())
            reg = cls(
                current_public_key=bytes.fromhex(data.get("current_public_key", "")),
                algorithm=data.get("algorithm", ED25519),
                registry_path=path,
            )
            rec = data.get("recovery_public_key")
            reg.recovery_public_key = bytes.fromhex(rec) if rec else None
            reg.recovery_algorithm = data.get("recovery_algorithm")
            reg.rotations = data.get("rotations", [])
            reg.revocations = data.get("revocations", [])
            return reg
        return cls(current_public_key=b"", algorithm=ED25519, registry_path=path)

    def save(self) -> None:
        if self.registry_path is None:
            raise ValueError("registry has no path")
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": REGISTRY_VERSION,
            "current_public_key": self.current_public_key.hex(),
            "algorithm": self.algorithm,
            "recovery_public_key": self.recovery_public_key.hex() if self.recovery_public_key else None,
            "recovery_algorithm": self.recovery_algorithm,
            "rotations": self.rotations,
            "revocations": self.revocations,
        }
        tmp = self.registry_path.with_name(f".{self.registry_path.name}.{os.urandom(4).hex()}.tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.registry_path)
        finally:
            tmp.unlink(missing_ok=True)

    # ── trust queries ─────────────────────────────────────────────────────
    def is_current(self, public_key: bytes) -> bool:
        return bool(self.current_public_key) and public_key == self.current_public_key

    def is_recovery(self, public_key: bytes) -> bool:
        return bool(self.recovery_public_key) and public_key == self.recovery_public_key

    def is_revoked(self, public_key: bytes) -> bool:
        return any(r["key"] == public_key.hex() for r in self.revocations)

    def is_registered(self, public_key: bytes) -> bool:
        """Any key ever authorized: current, recovery, or a participant in a
        recorded rotation (both the ``from`` predecessor and the ``to``
        successor). Used by notary verification so PRE-rotation history stays
        verifiable after a hardware move — the old key's entries remain valid
        even after it is rotated out or revoked (a key cannot un-sign
        history)."""
        if self.is_current(public_key) or self.is_recovery(public_key):
            return True
        hex_pub = public_key.hex()
        return any(
            r.get("from") == hex_pub or r.get("to") == hex_pub
            for r in self.rotations
        )

    # ── rotation / recovery / revocation ─────────────────────────────────
    def rotate(self, from_backend: SigningBackend, to_public_key: bytes,
               to_algorithm: str, reason: str) -> Dict[str, Any]:
        """Cross-signed successor endorsement: the CURRENT (from) key signs a
        statement endorsing the new key, then the registry advances."""
        body = {
            "type": "key-rotation",
            "from": from_backend.public_key_hex(),
            "to": to_public_key.hex(),
            "from_algorithm": from_backend.algorithm,
            "to_algorithm": to_algorithm,
            "rotated_at": _now_iso(),
            "reason": reason,
        }
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        record = dict(body)
        record["from_signature"] = from_backend.sign(canonical).hex()
        self.rotations.append(record)
        self.current_public_key = to_public_key
        self.algorithm = to_algorithm
        self.save()
        return record

    def register_recovery(self, recovery_public_key: bytes,
                          recovery_algorithm: str, reason: str) -> None:
        self.recovery_public_key = recovery_public_key
        self.recovery_algorithm = recovery_algorithm
        self.save()

    def revoke(self, key_to_revoke: bytes, reason: str,
               signer: SigningBackend) -> Dict[str, Any]:
        """Sign that ``key_to_revoke`` is retired. The signature is by the
        CURRENT key (the signer must be authorized). A revoked key can no
        longer sign NEW anchors, but its historical notary entries remain
        verifiable."""
        body = {
            "type": "key-revocation",
            "key": key_to_revoke.hex(),
            "revoked_at": _now_iso(),
            "reason": reason,
        }
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        record = dict(body)
        record["signature"] = signer.sign(canonical).hex()
        self.revocations.append(record)
        self.save()
        return record

    def verify_rotation(self, record: Dict[str, Any]) -> bool:
        """Verify a stored rotation record: the `from` key's signature over the
        rotation body (everything but from_signature) is valid."""
        try:
            pub = bytes.fromhex(record["from"])
            algo = record.get("from_algorithm", ED25519)
        except (KeyError, ValueError):
            return False
        body = {k: v for k, v in record.items() if k != "from_signature"}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        try:
            sig = bytes.fromhex(record["from_signature"])
        except (KeyError, ValueError):
            return False
        return verify_signature(canonical, sig, pub, algo)


class ChainAnchor:
    """Signs and verifies external chain-tip snapshots for an AuditChain."""

    def __init__(
        self,
        seed: Optional[bytes] = None,
        anchor_path: Optional[str | Path] = None,
        public_key: Optional[bytes] = None,
        algorithm: str = ED25519,
        backend: Optional[SigningBackend] = None,
        registry_path: Optional[str | Path] = None,
    ) -> None:
        """Signing via a ``backend`` (software or hardware) or a raw Ed25519
        ``seed``; verify-only via ``public_key`` + ``algorithm`` (e.g. a
        notarized copy on a machine without the key). ``registry_path``
        overrides the default key-registry location (next to the anchor)."""
        self._signer: Optional[SigningBackend] = None
        if backend is not None:
            self._signer = backend
            self._pub = backend.public_key_bytes()
            self._algorithm = backend.algorithm
        elif seed is not None:
            self._signer = SoftwareEd25519Backend(seed)
            self._pub = self._signer.public_key_bytes()
            self._algorithm = ED25519
        elif public_key is not None:
            self._pub = public_key
            self._algorithm = algorithm
        else:
            raise ValueError("ChainAnchor requires a seed, a public key, or a signing backend")
        self.anchor_path = Path(anchor_path) if anchor_path else None
        self.registry_path = Path(registry_path) if registry_path else None

    @property
    def public_key(self) -> bytes:
        return self._pub

    def public_key_hex(self) -> str:
        return self._pub.hex()

    @classmethod
    def from_env(cls) -> "ChainAnchor":
        """Load the key per MSB_CHAIN_ANCHOR_BACKEND (software default) + the
        keyfile/env/keychain seed. Fail-closed: a configured-but-unreadable
        key raises rather than degrading silently."""
        name = os.getenv(BACKEND_ENV, "software")
        if name != "software":
            return cls(backend=build_backend(name))
        raw = os.getenv(KEY_ENV)
        keyfile = _default_key_path()
        if raw is None and keyfile.exists():
            raw = keyfile.read_text().strip()
        if raw is None:
            raw = _seed_from_keychain()
        if raw is None:
            raise ValueError(
                f"no chain anchor key configured: set {KEY_ENV}, create {keyfile}, "
                "or store it in the macOS keychain (scripts/store-anchor-key.sh)"
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
            # Merkle root (P4): committed in the signed snapshot so a third
            # party holding ONLY this anchor + one inclusion receipt can
            # verify a single action without the whole chain.
            "merkle_root": chain.merkle_root(),
            "chain_sha256": chain_sha,
            "anchored_at": _now_iso(),
        }

    def _canonical(self, snapshot: Dict[str, Any]) -> bytes:
        return json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()

    def _sign(self, snapshot: Dict[str, Any]) -> bytes:
        if self._signer is None:
            raise ValueError("verify-only anchor cannot sign new snapshots")
        return self._signer.sign(self._canonical(snapshot))

    def _verify_signature(self, snapshot: Dict[str, Any], signature: bytes) -> bool:
        return verify_signature(self._canonical(snapshot), signature, self._pub, self._algorithm)

    # ── Key registry (rotation / recovery) ────────────────────────────────────
    def _registry(self, chain: AuditChain, *, bootstrap: bool = False) -> KeyRegistry:
        """The key registry for this chain. ``bootstrap=True`` (write paths)
        initializes the registry with the current key when none exists yet;
        read paths keep it read-only so verification never writes."""
        path = self.registry_path or _default_registry_path(Path(chain.db_path))
        reg = KeyRegistry.load(chain, path)
        if bootstrap and not reg.current_public_key:
            reg.current_public_key = self._pub
            reg.algorithm = self._algorithm
            reg.save()
        return reg

    def rotate(self, chain: AuditChain, new_backend: SigningBackend,
               reason: str) -> Dict[str, Any]:
        """Cross-signed key rotation: the CURRENT key endorses the new key,
        the registry advances, and the chain is re-anchored with the new key.
        The old key must be available to sign the endorsement (this is the
        planned-rotation path; use ``recover()`` when the old key is lost)."""
        if self._signer is None:
            raise ValueError("verify-only anchor cannot rotate keys")
        reg = self._registry(chain, bootstrap=True)
        if not reg.is_current(self._pub):
            raise ValueError(
                "cannot rotate: this key is not the registry's current key "
                "(use the current key, or --recover if it is lost)"
            )
        record = reg.rotate(self._signer, new_backend.public_key_bytes(),
                            new_backend.algorithm, reason)
        # Re-anchor with the NEW key so the anchor moves with the rotation.
        new_anchor = ChainAnchor(backend=new_backend, anchor_path=self.anchor_path,
                                 registry_path=self.registry_path)
        new_anchor.anchor(chain)
        return {"rotation": record, "anchored": new_anchor.verify(chain)}

    def register_recovery(self, chain: AuditChain, recovery_public_key: bytes,
                          recovery_algorithm: str = ED25519, reason: str = "") -> None:
        """Register an offline recovery key (public half only; the operator
        keeps the seed off-box). After registration, ``recover()`` can
        re-anchor if the primary key dies."""
        reg = self._registry(chain, bootstrap=True)
        reg.register_recovery(recovery_public_key, recovery_algorithm, reason)

    def recover(self, chain: AuditChain, recovery_backend: SigningBackend,
                reason: str) -> Dict[str, Any]:
        """Recovery path: re-anchor with the registered recovery key when the
        primary key is lost (enclave died, YubiKey lost). Fails closed if no
        recovery key was registered."""
        reg = self._registry(chain, bootstrap=True)
        if reg.recovery_public_key is None:
            raise ValueError(
                "no recovery key registered — recovery is impossible; the chain "
                "remains verifiable via the off-box notary but cannot be re-anchored"
            )
        pub = recovery_backend.public_key_bytes()
        if pub != reg.recovery_public_key:
            raise ValueError("recovery key does not match the registered recovery public key")
        if reg.is_revoked(pub):
            raise ValueError("recovery key was revoked — not accepted for re-anchoring")
        # Record the recovery as a rotation with the recovery key endorsing
        # itself (the old key is gone and cannot sign).
        record = reg.rotate(recovery_backend, pub, recovery_backend.algorithm,
                            reason or "recovery: primary key lost")
        new_anchor = ChainAnchor(backend=recovery_backend, anchor_path=self.anchor_path,
                                 registry_path=self.registry_path)
        new_anchor.anchor(chain)
        return {"rotation": record, "anchored": new_anchor.verify(chain)}

    def revoke(self, chain: AuditChain, key_to_revoke: Optional[bytes] = None,
               reason: str = "") -> Dict[str, Any]:
        """Revoke the current key (or an explicit key). The signature is by
        the current key itself. After revocation the key cannot sign NEW
        anchors; its historical notary entries remain verifiable."""
        if self._signer is None:
            raise ValueError("verify-only anchor cannot revoke keys")
        reg = self._registry(chain, bootstrap=True)
        target = key_to_revoke or self._pub
        if not reg.is_current(target) and not reg.is_registered(target):
            raise ValueError("cannot revoke a key that is not registered")
        return reg.revoke(target, reason, self._signer)

    # ── Anchor store ──────────────────────────────────────────────────────────
    def _store_path(self, chain: AuditChain) -> Path:
        return self.anchor_path or _default_anchor_path(Path(chain.db_path))

    def anchor(self, chain: AuditChain) -> Dict[str, Any]:
        """Sign the current chain tip and persist the snapshot + signature."""
        if self._signer is None:
            raise ValueError("verify-only anchor cannot sign new snapshots")
        snapshot = self._snapshot(chain)
        signature = self._sign(snapshot)
        record = {
            "snapshot": snapshot,
            "signature": signature.hex(),
            "public_key": self._pub.hex(),
            "key_algorithm": self._algorithm,
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
        anchor_pub = bytes.fromhex(anchor["public_key"])
        recorded_algorithm = anchor.get("key_algorithm", ED25519)
        # Key authorization: the anchor must be signed by a key that is
        # authorized per the key registry (the current key or a registered
        # recovery key). A random key that re-anchored the chain is caught
        # here even if it self-describes as "current". Backward compatible:
        # with no registry file yet, the verifying key itself is treated as
        # current (legacy single-key behavior).
        reg = self._registry(chain)
        if not reg.current_public_key:
            reg.current_public_key = self._pub
            reg.algorithm = self._algorithm
        authorized = reg.is_current(anchor_pub) or reg.is_recovery(anchor_pub)
        if not authorized:
            return {"valid": False,
                    "reason": "anchor public key is not the registry's current key or recovery key",
                    "anchored_tip": snapshot.get("tip_hash")}
        if reg.is_revoked(anchor_pub):
            return {"valid": False, "reason": "anchor signed by a REVOKED key",
                    "anchored_tip": snapshot.get("tip_hash")}
        if anchor_pub != self._pub:
            # Anchor signed by the registered recovery key while we verify
            # with the primary — acceptable (recovery is authorized), but
            # report it so the operator knows the anchor key changed.
            if not reg.is_recovery(anchor_pub):
                return {"valid": False,
                        "reason": "anchor public key does not match the verifying key",
                        "anchored_tip": snapshot.get("tip_hash")}
        if recorded_algorithm != self._algorithm and recorded_algorithm != reg.recovery_algorithm:
            return {"valid": False,
                    "reason": f"anchor key algorithm mismatch: {recorded_algorithm} != {self._algorithm}",
                    "anchored_tip": snapshot.get("tip_hash")}
        if not self._verify_signature(snapshot, signature):
            return {"valid": False, "reason": "anchor signature invalid — anchor file tampered",
                    "anchored_tip": snapshot.get("tip_hash")}
        signer_note = "recovery-key" if reg.is_recovery(anchor_pub) else "current-key"
        live = self._snapshot(chain)
        if live["tip_hash"] != snapshot["tip_hash"] or live["seq"] != snapshot["seq"]:
            # Distinguish STALE from REPLACEMENT: if the anchored tip still
            # exists inside the live chain, the chain is a superset — records
            # were appended after the anchor (re-anchoring stopped). If the
            # anchored tip is ABSENT, the history itself was swapped.
            anchored_tip_in_live = any(
                r.record_hash == snapshot["tip_hash"] for r in chain.get_chain()
            )
            if anchored_tip_in_live and live["seq"] > snapshot["seq"]:
                stale_seconds = _chain_newer_than_anchor(chain, snapshot["anchored_at"])
                return {
                    "valid": False,
                    "stale": True,
                    "stale_seconds": stale_seconds,
                    "reason": f"anchor is STALE — {live['seq'] - snapshot['seq']} newer records "
                              f"not covered (re-anchoring stopped)",
                    "anchored_tip": snapshot["tip_hash"],
                    "anchored_seq": snapshot["seq"],
                    "live_tip": live["tip_hash"],
                    "live_seq": live["seq"],
                }
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
        # Merkle-root cross-check (P4): anchors written after the Merkle
        # upgrade commit the root; it must match the live chain's root, so a
        # rewritten chain that somehow keeps tip + sha256 identical (or an
        # anchor copy/pasted onto a different chain) is still caught. Pre-
        # Merkle anchors lack the field and verify unchanged (backward
        # compatible — the signature already covers whatever was committed).
        if "merkle_root" in snapshot and live.get("merkle_root") != snapshot["merkle_root"]:
            return {"valid": False, "reason": "merkle root mismatch — chain content changed under the anchor",
                    "anchored_merkle_root": snapshot["merkle_root"],
                    "live_merkle_root": live.get("merkle_root")}
        # Staleness of a VALID anchor: chain records newer than the signed
        # anchor mean re-anchoring stopped after the anchor — reported, with
        # the anchor itself still valid for the tip it covers.
        stale_seconds = _chain_newer_than_anchor(chain, snapshot["anchored_at"])
        return {"valid": True, "record_count": live["record_count"],
                "tip_hash": live["tip_hash"], "anchored_at": snapshot["anchored_at"],
                "stale": stale_seconds > 0, "stale_seconds": stale_seconds,
                "signer": signer_note}

    def build_notary_entry(self, chain: AuditChain) -> Dict[str, Any]:
        """Build ONE notary entry (``{"notarized_at", "anchor"}``) without
        writing it — the primitive ``notarize()`` and ``NotaryService`` both
        compose on, so the off-box push can stamp the exact bytes that were
        appended."""
        anchor = self._read_anchor(chain)
        if anchor is None:
            self.anchor(chain)
            anchor = self._read_anchor(chain)
        return {"notarized_at": _now_iso(), "anchor": anchor}

    def notarize(self, chain: AuditChain, dest: str | Path, *, append: bool = True) -> Path:
        """Export the current signed anchor out-of-band.

        ``append=True`` (default) writes one compact JSON line per call — an
        append-only JSONL notary log, where each entry is independently
        verifiable and the newest entry always covers the newest tip.
        ``append=False`` writes a single pretty-printed bare anchor copy,
        directly usable as an anchor store on a verify-only machine.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        entry = self.build_notary_entry(chain)
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        if append:
            with dest.open("a") as handle:
                handle.write(line + "\n")
        else:
            # bare anchor copy — directly usable as an anchor store
            dest.write_text(json.dumps(entry["anchor"], indent=2, sort_keys=True) + "\n")
        return dest

    def verify_notary_entry(self, entry: Dict[str, Any], chain: AuditChain) -> Dict[str, Any]:
        """Validate ONE notary entry: (1) signed by the same key with a valid
        signature, (2) its tip is present in the live chain, and (3) when the
        entry carries an RFC 3161 ``timestamp`` proof, that proof is verified
        AND cryptographically covers this exact entry (recomputed from the
        entry with the timestamp field stripped, the bytes that were stamped).
        Fail-closed: an unverified or non-covering timestamp makes the entry
        invalid, never silently accepted."""
        anchor = entry.get("anchor")
        if not isinstance(anchor, dict) or "snapshot" not in anchor:
            return {"valid": False, "reason": "notary entry has no anchor snapshot"}
        snapshot = anchor["snapshot"]
        signature = bytes.fromhex(anchor["signature"]) if isinstance(anchor.get("signature"), str) else b""
        pub = bytes.fromhex(anchor["public_key"]) if isinstance(anchor.get("public_key"), str) else b""
        # Key authorization for NOTARY entries is deliberately broader than
        # for the live anchor: any key ever registered (current, recovery, or
        # a cross-signed successor) may legitimately appear in history, so a
        # hardware rotation does not invalidate the pre-rotation notary log.
        # An entirely unknown key (never registered) is still rejected.
        reg = self._registry(chain)
        # Legacy fallback: with no registry file yet, the verifying key is the
        # current key (single-key deployments predate the registry).
        if not reg.current_public_key:
            reg.current_public_key = self._pub
            reg.algorithm = self._algorithm
        if not reg.is_registered(pub):
            return {"valid": False, "reason": "notary entry signed by a key that is not registered (current, recovery, or successor)"}
        # Verify with the entry's OWN public key + recorded algorithm: the
        # signature must hold under the key that actually signed it, whatever
        # the verifying anchor's current key is (rotation-safe).
        recorded_algo = anchor.get("key_algorithm", ED25519)
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        try:
            sig_valid = verify_signature(canonical, signature, pub, recorded_algo)
        except ValueError:
            sig_valid = False
        if not sig_valid:
            return {"valid": False, "reason": "notary entry signature invalid — notary log tampered"}
        tip = snapshot.get("tip_hash", "")
        if not any(r.record_hash == tip for r in chain.get_chain()):
            return {
                "valid": False,
                "reason": "notarized tip is not in the live chain — whole-DB rollback or replacement (T7)",
                "notarized_tip": tip,
                "notarized_at": entry.get("notarized_at"),
            }
        result: Dict[str, Any] = {
            "valid": True,
            "tip_hash": tip,
            "record_count": snapshot.get("record_count"),
        }
        ts = entry.get("timestamp")
        if isinstance(ts, dict) and ts.get("digest_sha256"):
            proof = TimestampProof.from_dict(ts)
            canonical = json.dumps(
                {k: v for k, v in entry.items() if k != "timestamp"},
                sort_keys=True, separators=(",", ":"),
            ).encode()
            if proof.digest_sha256 != hashlib.sha256(canonical).hexdigest():
                return {"valid": False, "reason": "timestamp proof does not cover this entry"}
            if proof.source == "rfc3161" and not proof.verified:
                return {"valid": False, "reason": "RFC 3161 timestamp proof was not verified"}
            result["timestamp_valid"] = True
            result["timestamp_source"] = proof.source
            result["timestamp_gen_time"] = proof.gen_time
        return result

    def verify_notary(self, chain: AuditChain, log: str | Path) -> Dict[str, Any]:
        """Verify the most recent entry in an out-of-band notary log.

        The notary log is an append-only JSONL of signed anchor snapshots
        (``notarize()`` output). The LAST entry must be a valid entry per
        ``verify_notary_entry``: signed by the same key, its tip present in
        the live chain (a whole-DB rollback that also replaces the local
        anchor file is still caught here — the notary holds the signed tip
        the rolled-back chain no longer contains), and any RFC 3161 timestamp
        proof must cover it.
        """
        path = Path(log)
        if not path.exists():
            return {"valid": False, "reason": f"notary log not found: {path}"}
        lines = [line for line in path.read_text().splitlines() if line.strip()]
        if not lines:
            return {"valid": False, "reason": "notary log is empty"}
        try:
            entry = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            return {"valid": False, "reason": f"last notary entry is not valid JSON: {exc}"}
        result = self.verify_notary_entry(entry, chain)
        if not result.get("valid"):
            return result
        result["notarized_at"] = entry.get("notarized_at")
        result["entry_count"] = len(lines)
        return result


class AnchoredAuditChain:
    """AuditChain wrapper that re-anchors after every append, so the external
    anchor never goes stale. Duck-types the AuditChain surface used by the
    vesta services (append / verify_chain / get_chain)."""

    def __init__(self, chain: AuditChain, anchor: ChainAnchor) -> None:
        self.chain = chain
        self.anchor = anchor
        # Mark the inner chain as anchored so AuditChain.append's fail-closed
        # guard (uac.audit_chain) lets this wrapper through: it re-anchors
        # after every append, which is the only sanctioned append path to the
        # production chain when a key is configured.
        chain._anchored = True
        # Establish the initial anchor so verification is meaningful from birth.
        # NEVER clobber an existing anchor signed by an UNAUTHORIZED key (found
        # live: a test process re-anchored the production chain with a random
        # key, silently rotating the anchor). A REGISTERED successor or
        # recovery key is allowed through — that is the rotation ceremony.
        existing = self.anchor._read_anchor(chain)
        if existing is not None:
            existing_pub = bytes.fromhex(existing["public_key"])
            reg = self.anchor._registry(chain)
            authorized = (
                existing_pub == anchor._pub
                or reg.is_recovery(existing_pub)
                or reg.is_current(existing_pub)
            )
            if not authorized:
                raise ValueError(
                    "chain anchor exists with a different signing key — refusing to clobber; "
                    "rotate explicitly with --rotate (planned) or --recover (lost key)"
                )
        if existing is None:
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
    """Factory used at service wiring sites: anchored when a key OR a
    non-software backend is configured (e.g. MSB_CHAIN_ANCHOR_BACKEND=
    secure-enclave) OR a keychain item is configured
    (MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE), plain AuditChain otherwise (zero
    behavior change without a key). Anchoring requested but unavailable must
    never degrade silently to an unsigned chain: a configured-but-unprovisioned
    hardware backend raises via ChainAnchor.from_env()."""
    backend = os.getenv(BACKEND_ENV, "software")
    raw = os.getenv(KEY_ENV)
    keychain = os.getenv("MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE", "").strip()
    if (
        backend == "software"
        and raw is None
        and not _default_key_path().exists()
        and not keychain
    ):
        return AuditChain()
    return AnchoredAuditChain(AuditChain(), ChainAnchor.from_env())


def _verify_daemon(db_path: str, *, notify: bool, auto_anchor: bool = False) -> int:
    """One-shot health check for the launchd job: internal chain + anchored
    state, a macOS notification when unhealthy, a state file for dashboards,
    and a one-line status. Exit 0 = healthy, 2 = action needed.

    With ``auto_anchor``, a benignly STALE anchor (the anchored tip is still
    a valid prefix of the live chain — newer records were appended after the
    last anchor, e.g. by a keyless background process) is re-signed against
    the current tip instead of alerting. REPLACEMENT (anchored tip absent),
    tamper, wrong key, missing anchor, and broken chains still alert.
    """
    import subprocess

    chain = AuditChain(db_path)
    anchor = ChainAnchor.from_env()
    internal = chain.verify_chain()
    anchored = anchor.verify(chain)
    auto_reanchored = False
    if (
        auto_anchor
        and internal.get("valid")
        and anchored.get("valid") is False
        and anchored.get("stale", False) is True
    ):
        anchor.anchor(chain)
        anchored = anchor.verify(chain)
        auto_reanchored = True
    healthy = bool(internal.get("valid") and anchored.get("valid") and not anchored.get("stale", False))
    state = {
        "checked_at": _now_iso(),
        "healthy": healthy,
        "db": str(Path(db_path).resolve()),
        "internal_valid": internal.get("valid"),
        "internal_reason": internal.get("reason"),
        "anchored_valid": anchored.get("valid"),
        "stale": anchored.get("stale", False),
        "stale_seconds": anchored.get("stale_seconds", 0),
        "reason": anchored.get("reason"),
        "record_count": anchored.get("record_count", internal.get("record_count")),
        "auto_reanchored": auto_reanchored,
    }
    state_dir = Path(os.getenv("MSB_ANCHOR_STATE_DIR", str(Path.home() / ".trinity" / "state")))
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "chain_anchor.json").write_text(json.dumps(state, indent=2))

    if healthy:
        print(f"OK chain_anchor internal={internal.get('valid')} anchored={anchored.get('valid')} "
              f"records={state['record_count']} anchor={anchored.get('anchored_at', '')}"
              + (" auto_reanchored=1" if auto_reanchored else ""))
        return 0
    problem = state["reason"] or ("internal chain broken" if not internal.get("valid") else "unknown")
    print(f"ALERT chain_anchor: {problem}")
    if notify and sys.platform == "darwin":
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{problem[:120]}" with title "MSB chain anchor" '
                 f'subtitle "ACTION NEEDED — audit chain {state["record_count"]} records"'],
                check=False, capture_output=True, timeout=15,
            )
        except Exception as exc:  # noqa: BLE001 — notification must never mask the alert
            print(f"  (notification failed: {exc})")
    return 2


def _main() -> int:
    parser = argparse.ArgumentParser(description="External chain-tip anchor (T7 fix)")
    parser.add_argument("--verify", metavar="AUDIT_DB", help="verify the chain against its external anchor")
    parser.add_argument("--anchor", metavar="AUDIT_DB", help="sign and persist a fresh anchor for the chain")
    parser.add_argument("--notarize", metavar="AUDIT_DB", help="append a signed anchor snapshot to an out-of-band notary log")
    parser.add_argument("--verify-notary", metavar="AUDIT_DB", help="verify the last notary log entry against the chain")
    parser.add_argument("--notary", metavar="LOG", help="notary log path (required with --notarize / --verify-notary)")
    parser.add_argument("--verify-daemon", metavar="AUDIT_DB", help="one-shot health check for launchd (alert on problem)")
    parser.add_argument("--auto-anchor", action="store_true", help="re-sign a benignly STALE anchor instead of alerting (with --verify-daemon)")
    parser.add_argument("--no-notify", action="store_true", help="suppress the macOS notification (testing)")
    # Merkle proof-of-inclusion (P4)
    parser.add_argument("--receipt", metavar="AUDIT_DB", help="emit a Merkle inclusion receipt for one record")
    parser.add_argument("--seq", metavar="INT", type=int, help="record seq for --receipt (1-based)")
    parser.add_argument("--verify-receipt", metavar="AUDIT_DB", help="verify a receipt JSON file against the chain")
    parser.add_argument("--receipt-file", metavar="JSON", help="receipt file for --verify-receipt")
    # Key rotation / recovery ceremony
    parser.add_argument("--rotate", metavar="AUDIT_DB", help="cross-signed key rotation: current key endorses a new backend")
    parser.add_argument("--register-recovery", metavar="AUDIT_DB", help="register an offline recovery public key (seed stays off-box)")
    parser.add_argument("--recovery-public-key", metavar="HEX", help="public key to register with --register-recovery")
    parser.add_argument("--recovery-algorithm", metavar="ALG", default=ED25519, help="algorithm of the recovery key (default ed25519)")
    parser.add_argument("--revoke", metavar="AUDIT_DB", help="revoke the current key (cannot sign NEW anchors; history stays valid)")
    parser.add_argument("--recover", metavar="AUDIT_DB", help="re-anchor with the registered recovery key (primary key lost)")
    parser.add_argument("--seed", metavar="HEX", help="new/recovery key seed (with --rotate software or --recover)")
    parser.add_argument("--backend", metavar="NAME", default="", help="new backend name for --rotate (secure-enclave / yubikey / software)")
    parser.add_argument("--reason", metavar="TEXT", default="", help="rotation / revocation / recovery reason")
    args = parser.parse_args()
    if args.verify_daemon:
        return _verify_daemon(args.verify_daemon, notify=not args.no_notify, auto_anchor=args.auto_anchor)
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
    if args.receipt:
        if not args.seq:
            parser.error("--receipt requires --seq <int>")
        chain = AuditChain(args.receipt)
        proof = chain.inclusion_proof(seq=args.seq)
        # Include the committed root from the signed anchor when one exists,
        # so the receipt carries the externally-verifiable root, not just the
        # chain's self-reported one.
        committed = None
        try:
            anchor_record = ChainAnchor.from_env()._read_anchor(chain)
            if anchor_record:
                committed = anchor_record.get("snapshot", {}).get("merkle_root")
        except Exception:  # noqa: BLE001 — receipt emission must not fail on anchor quirks
            committed = None
        print(json.dumps({
            "receipt": {
                "seq": proof.seq, "leaf_hash": proof.leaf_hash,
                "leaf_index": proof.leaf_index, "tree_size": proof.tree_size,
                "root": proof.root, "path": proof.path,
            },
            "committed_merkle_root": committed,
            "valid_against_chain": chain.verify_inclusion(
                proof, expected_root=committed or proof.root),
        }, indent=2))
        return 0
    if args.verify_receipt:
        if not args.receipt_file:
            parser.error("--verify-receipt requires --receipt-file <json>")
        chain = AuditChain(args.verify_receipt)
        with open(args.receipt_file) as handle:
            data = json.load(handle)
        from msb_ledger.merkle import InclusionProof

        proof = InclusionProof(**data["receipt"])
        committed = data.get("committed_merkle_root")
        valid = chain.verify_inclusion(proof, expected_root=committed or None)
        print(json.dumps({"valid": valid, "seq": proof.seq,
                          "root": proof.root,
                          "committed_merkle_root": committed}, indent=2))
        return 0 if valid else 1
    if args.rotate:
        if not args.backend:
            parser.error("--rotate requires --backend <secure-enclave|yubikey|software> "
                          "(and --seed <hex> when the backend is software)")
        if args.backend == "software" and not args.seed:
            parser.error("--rotate --backend software requires --seed <hex> for the new key")
        chain = AuditChain(args.rotate)
        anchor = ChainAnchor.from_env()
        new_backend: SigningBackend
        if args.backend == "software":
            new_backend = SoftwareEd25519Backend(bytes.fromhex(args.seed))
        else:
            new_backend = build_backend(args.backend)
        print(json.dumps(anchor.rotate(chain, new_backend, args.reason), indent=2))
        return 0
    if args.register_recovery:
        if not args.recovery_public_key:
            parser.error("--register-recovery requires --recovery-public-key <hex>")
        chain = AuditChain(args.register_recovery)
        anchor = ChainAnchor.from_env()
        anchor.register_recovery(chain, bytes.fromhex(args.recovery_public_key),
                                 args.recovery_algorithm, args.reason)
        print(json.dumps({"registered_recovery_public_key": args.recovery_public_key}, indent=2))
        return 0
    if args.revoke:
        chain = AuditChain(args.revoke)
        anchor = ChainAnchor.from_env()
        record = anchor.revoke(chain, reason=args.reason)
        print(json.dumps({"revoked": record}, indent=2))
        return 0
    if args.recover:
        if not args.seed:
            parser.error("--recover requires --seed <hex> (the recovery key seed)")
        chain = AuditChain(args.recover)
        anchor = ChainAnchor.from_env()
        recovery = SoftwareEd25519Backend(bytes.fromhex(args.seed))
        print(json.dumps(anchor.recover(chain, recovery, args.reason), indent=2))
        return 0
    if args.notarize or args.verify_notary:
        if not args.notary:
            parser.error("--notarize and --verify-notary require --notary <log>")
        chain = AuditChain(args.notarize or args.verify_notary)
        anchor = ChainAnchor.from_env()
        if args.notarize:
            dest = anchor.notarize(chain, args.notary)
            current = anchor._read_anchor(chain)
            tip = current["snapshot"]["tip_hash"] if current else ""
            print(json.dumps({"notarized": str(dest), "tip_hash": tip}, indent=2))
            return 0
        report = anchor.verify_notary(chain, args.notary)
        print(json.dumps(report, indent=2))
        return 0 if report["valid"] else 2
    parser.error("specify --verify, --anchor, --notarize, --verify-notary, --verify-daemon, "
                  "--rotate, --register-recovery, --revoke, or --recover")


if __name__ == "__main__":
    raise SystemExit(_main())
