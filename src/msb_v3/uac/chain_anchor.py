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
    python -m msb_v3.uac.chain_anchor --notarize <audit.db> --notary <log>
    python -m msb_v3.uac.chain_anchor --verify-notary <audit.db> --notary <log>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from msb_v3.core.config import settings
from msb_v3.uac.audit_chain import AuditChain
from msb_v3.uac.signing import (
    ED25519,
    SigningBackend,
    SoftwareEd25519Backend,
    build_backend,
    verify_signature,
)
from msb_v3.uac.timestamping import TimestampProof

ANCHOR_FILENAME = "chain_anchor.json"
KEY_ENV = "MSB_CHAIN_ANCHOR_KEY"
BACKEND_ENV = "MSB_CHAIN_ANCHOR_BACKEND"
_VERSION = 1


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


def _default_anchor_path(db_path: Path) -> Path:
    return Path(db_path).with_name(ANCHOR_FILENAME)


def generate_seed() -> bytes:
    """Return a fresh 32-byte Ed25519 private seed."""
    return Ed25519PrivateKey.generate().private_bytes_raw()


class ChainAnchor:
    """Signs and verifies external chain-tip snapshots for an AuditChain."""

    def __init__(
        self,
        seed: Optional[bytes] = None,
        anchor_path: Optional[str | Path] = None,
        public_key: Optional[bytes] = None,
        algorithm: str = ED25519,
        backend: Optional[SigningBackend] = None,
    ) -> None:
        """Signing via a ``backend`` (software or hardware) or a raw Ed25519
        ``seed``; verify-only via ``public_key`` + ``algorithm`` (e.g. a
        notarized copy on a machine without the key)."""
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

    @classmethod
    def from_env(cls) -> "ChainAnchor":
        """Load the key per MSB_CHAIN_ANCHOR_BACKEND (software default) + the
        keyfile/env seed. Fail-closed: a configured-but-unreadable key raises
        rather than degrading silently."""
        name = os.getenv(BACKEND_ENV, "software")
        if name != "software":
            return cls(backend=build_backend(name))
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

    def _canonical(self, snapshot: Dict[str, Any]) -> bytes:
        return json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()

    def _sign(self, snapshot: Dict[str, Any]) -> bytes:
        if self._signer is None:
            raise ValueError("verify-only anchor cannot sign new snapshots")
        return self._signer.sign(self._canonical(snapshot))

    def _verify_signature(self, snapshot: Dict[str, Any], signature: bytes) -> bool:
        return verify_signature(self._canonical(snapshot), signature, self._pub, self._algorithm)

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
        pub_matches = bytes.fromhex(anchor["public_key"]) == self._pub
        if not pub_matches:
            return {"valid": False, "reason": "anchor public key does not match the verifying key",
                    "anchored_tip": snapshot.get("tip_hash")}
        recorded_algorithm = anchor.get("key_algorithm", ED25519)
        if recorded_algorithm != self._algorithm:
            return {"valid": False,
                    "reason": f"anchor key algorithm mismatch: {recorded_algorithm} != {self._algorithm}",
                    "anchored_tip": snapshot.get("tip_hash")}
        if not self._verify_signature(snapshot, signature):
            return {"valid": False, "reason": "anchor signature invalid — anchor file tampered",
                    "anchored_tip": snapshot.get("tip_hash")}
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
        # Staleness of a VALID anchor: chain records newer than the signed
        # anchor mean re-anchoring stopped after the anchor — reported, with
        # the anchor itself still valid for the tip it covers.
        stale_seconds = _chain_newer_than_anchor(chain, snapshot["anchored_at"])
        return {"valid": True, "record_count": live["record_count"],
                "tip_hash": live["tip_hash"], "anchored_at": snapshot["anchored_at"],
                "stale": stale_seconds > 0, "stale_seconds": stale_seconds}

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
        if pub != self._pub:
            return {"valid": False, "reason": "notary entry signed by a different key than the current anchor key"}
        if anchor.get("key_algorithm", ED25519) != self._algorithm:
            return {"valid": False, "reason": "notary entry key algorithm does not match the current anchor key"}
        if not self._verify_signature(snapshot, signature):
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
        # NEVER clobber an existing anchor signed by a DIFFERENT key (found
        # live: a test process re-anchored the production chain with a random
        # key, silently rotating the anchor). Key changes are explicit operator
        # actions (`--anchor`), not something an init path may do silently.
        existing = self.anchor._read_anchor(chain)
        if existing is not None and bytes.fromhex(existing["public_key"]) != anchor._pub:
            raise ValueError(
                "chain anchor exists with a different signing key — refusing to clobber; "
                "re-anchor explicitly with --anchor to rotate the key"
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
    secure-enclave), plain AuditChain otherwise (zero behavior change without
    a key). Anchoring requested but unavailable must never degrade silently
    to an unsigned chain: a configured-but-unprovisioned hardware backend
    raises via ChainAnchor.from_env()."""
    backend = os.getenv(BACKEND_ENV, "software")
    raw = os.getenv(KEY_ENV)
    if backend == "software" and raw is None and not _default_key_path().exists():
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
    parser.error("specify --verify, --anchor, --notarize, --verify-notary, or --verify-daemon")


if __name__ == "__main__":
    raise SystemExit(_main())
