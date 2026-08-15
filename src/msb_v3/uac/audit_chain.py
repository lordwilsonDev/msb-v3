"""Audit Chain — genuine hash-chained, tamper-evident audit trail for UAC.

Distinct from triumvirate/argus_auditor.py, which is a pattern-scanning
health-check linter (greps for FIXME/drift/ERROR keywords) with a plain,
non-chained SQLite log — not immutable provenance despite the superficially
similar name. This module is what the frozen UAC spec's Sovereign Runtime
Cross-Cutting System 8 actually means by "Merkle Audit Chain: immutable
provenance, evolution history" — confirmed absent from the codebase by
direct search before this was written (2026-08-02).

Not a full Merkle tree (no branching/proof-of-inclusion) — a hash chain:
each record's hash covers its own content plus the previous record's hash,
so any edit to any past record breaks every hash after it. That satisfies
"tamper-evident" without the added complexity of tree structures this
scale doesn't need yet.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from msb_v3.core.config import settings

_RUNTIME_ROOT = Path(settings.db_path).parent / "uac"
_AUDIT_DB = _RUNTIME_ROOT / "audit_chain.db"
_GENESIS_HASH = "0" * 64

# Mirror of chain_anchor.KEY_ENV / chain_anchor._default_key_path(): the
# audit-chain module cannot import chain_anchor (circular), so the
# fail-closed guard re-checks the same two signals. Keep in sync with
# uac/chain_anchor.py.
_KEY_ENV = "MSB_CHAIN_ANCHOR_KEY"
_ALLOW_KEYLESS_ENV = "MSB_ALLOW_KEYLESS_APPENDS"


class AuditChainKeylessAppendError(RuntimeError):
    """Append refused: an anchored chain is configured but this chain is bare.

    Raised when a non-anchored ``AuditChain`` appends to the default
    production chain while an anchor key is configured — the record would
    land unanchored and only heal when the daily verify job re-signs.
    External automation must carry the anchor key (use
    ``anchored_chain_from_env()``) or route through the server.
    """


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_records (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                component TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                record_hash TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chain_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )


@dataclass
class AuditRecord:
    seq: int
    component: str
    event_type: str
    payload: Dict[str, Any]
    timestamp: str
    prev_hash: str
    record_hash: str


def _compute_hash(prev_hash: str, component: str, event_type: str, payload: Dict[str, Any], timestamp: str) -> str:
    canonical = json.dumps(
        {
            "prev_hash": prev_hash,
            "component": component,
            "event_type": event_type,
            "payload": payload,
            "timestamp": timestamp,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class AuditChainLike(Protocol):
    """Structural type for the audit-chain surface (append/verify/get).

    Both ``AuditChain`` and the ``AnchoredAuditChain`` wrapper from
    ``uac.chain_anchor`` satisfy it, so services that consume an audit chain
    can accept either without importing the wrapper (which would be a
    circular import). Services should type their injected chain as this so
    anchored chains can be wired in anywhere.
    """

    @property
    def db_path(self) -> Path: ...

    def append(self, component: str, event_type: str, payload: Dict[str, Any]) -> AuditRecord: ...
    def verify_chain(self) -> Dict[str, Any]: ...
    def get_chain(self, component: Optional[str] = None) -> list: ...


def _chain_key_configured() -> bool:
    return os.getenv(_KEY_ENV) is not None or _default_anchor_key_path().exists()


def _default_anchor_key_path() -> Path:
    return Path(settings.msb_home) / "data" / "uac" / "chain_anchor_key"


def _is_default_chain(db_path: Path) -> bool:
    """True when ``db_path`` is the production chain the anchor covers."""
    try:
        return Path(db_path).resolve() == _AUDIT_DB.resolve()
    except OSError:
        return False


class AuditChain:
    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        allow_keyless: bool = False,
    ) -> None:
        self.db_path = Path(db_path) if db_path else _AUDIT_DB
        # Set True by the AnchoredAuditChain wrapper (uac.chain_anchor): the
        # wrapper re-anchors after every append, so its inner chain is the
        # sanctioned append path to the production chain.
        self._anchored = False
        # Explicit escape hatch for automation that cannot carry the anchor
        # key yet (dev/test fixtures, legacy processes mid-migration).
        self._allow_keyless = allow_keyless
        _init_db(self.db_path)

    def _conn(self) -> sqlite3.Connection:
        # timeout=10.0: under a saturated shared box a concurrent writer can
        # hold the RESERVED lock past Python's default 5s busy wait, so
        # BEGIN IMMEDIATE raises "database is locked" and the append is lost
        # from the caller's perspective (the phase-2 chaos suite observed
        # 300/400 concurrent appends landing under load). A longer busy wait
        # keeps the documented "sqlite serializes writes" contract intact.
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _last_hash(self, conn: sqlite3.Connection) -> str:
        row = conn.execute("SELECT record_hash FROM audit_records ORDER BY seq DESC LIMIT 1").fetchone()
        return row["record_hash"] if row else _GENESIS_HASH

    def _refuse_keyless_append(self) -> None:
        """Fail closed: no keyless appends to the production chain.

        When an anchor key is configured, appending to the DEFAULT chain
        through a bare ``AuditChain()`` would silently break the re-anchor
        invariant (the record lands unanchored and the chain only heals when
        the daily verify job re-signs it). Refuse instead — the only
        sanctioned append path to the production chain is the
        ``AnchoredAuditChain`` wrapper. Separate chains (node perimeter,
        tests, custom DBs) are unaffected. Escape hatches:
        ``AuditChain(..., allow_keyless=True)`` or
        ``MSB_ALLOW_KEYLESS_APPENDS=1`` (both for explicit dev/test use).
        """
        if self._anchored or self._allow_keyless:
            return
        if not _is_default_chain(self.db_path):
            return
        if os.getenv(_ALLOW_KEYLESS_ENV) == "1":
            return
        if not _chain_key_configured():
            return
        raise AuditChainKeylessAppendError(
            "keyless append refused: MSB_CHAIN_ANCHOR_KEY is configured and this "
            "bare AuditChain() targets the production chain (data/uac/audit_chain.db). "
            "Use anchored_chain_from_env() so the append re-anchors, or set "
            "MSB_ALLOW_KEYLESS_APPENDS=1 to opt out explicitly (dev/test only)."
        )

    def append(self, component: str, event_type: str, payload: Dict[str, Any]) -> AuditRecord:
        self._refuse_keyless_append()
        timestamp = _now_iso()
        with self._conn() as conn:
            # BEGIN IMMEDIATE acquires the write lock BEFORE the prev-hash
            # read, so two threads cannot both read the same tail and fork
            # the chain (the classic read-then-write race that silently
            # corrupts a hash chain under concurrency — found by the phase-2
            # chaos suite's concurrent-append test). The read+insert now run
            # inside one write transaction.
            conn.execute("BEGIN IMMEDIATE")
            prev_hash = self._last_hash(conn)
            record_hash = _compute_hash(prev_hash, component, event_type, payload, timestamp)
            cur = conn.execute(
                """
                INSERT INTO audit_records(component, event_type, payload, timestamp, prev_hash, record_hash)
                VALUES (?,?,?,?,?,?)
                """,
                (component, event_type, json.dumps(payload, ensure_ascii=False), timestamp, prev_hash, record_hash),
            )
            seq = cur.lastrowid
            if seq is None:
                raise RuntimeError("audit insert did not return a rowid")
        return AuditRecord(
            seq=seq, component=component, event_type=event_type, payload=payload,
            timestamp=timestamp, prev_hash=prev_hash, record_hash=record_hash,
        )

    def verify_chain(self) -> Dict[str, Any]:
        """Recompute every record's hash from its content and check it against
        both the stored hash and the next record's prev_hash. Returns the first
        break found, if any — an audit chain with a break is compromised from
        that point forward, not just at the broken record."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM audit_records ORDER BY seq ASC").fetchall()
        expected_prev = _GENESIS_HASH
        for row in rows:
            payload = json.loads(row["payload"])
            recomputed = _compute_hash(row["prev_hash"], row["component"], row["event_type"], payload, row["timestamp"])
            if row["prev_hash"] != expected_prev:
                return {"valid": False, "broken_at_seq": row["seq"], "reason": "prev_hash does not match preceding record"}
            if recomputed != row["record_hash"]:
                return {"valid": False, "broken_at_seq": row["seq"], "reason": "stored hash does not match recomputed content hash"}
            expected_prev = row["record_hash"]
        return {"valid": True, "record_count": len(rows)}

    def _set_meta(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO chain_meta(key, value) VALUES(?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def _get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM chain_meta WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def quarantine(self) -> Dict[str, Any]:
        """Explicitly quarantine the chain if tampering is detected.

        Does NOT silently heal: it verifies the chain, and if a break is found
        it records the compromised state (broken seq, reason, timestamp) in
        chain_meta so the compromise is explicit and discoverable. A
        quarantined chain remains verifiable as broken until `repair()` is run
        under operator control.
        """
        result = self.verify_chain()
        if result.get("valid"):
            self._set_meta("state", "active")
            self._set_meta("broken_at_seq", "")
            return {"quarantined": False, "reason": "chain already valid"}
        broken = result.get("broken_at_seq")
        self._set_meta("state", "quarantined")
        self._set_meta("broken_at_seq", str(broken))
        self._set_meta("reason", str(result.get("reason")))
        self._set_meta("quarantined_at", _now_iso())
        return {
            "quarantined": True,
            "broken_at_seq": broken,
            "reason": result.get("reason"),
            "state": "quarantined",
        }

    def repair(self) -> Dict[str, Any]:
        """Cascade-rewrite the chain from the first broken record.

        Checkpoint recovery: re-anchor at the last verified record's hash,
        recompute every record from the first break forward, then append an
        explicit, auditable "chain.repaired" event documenting the break point
        and anchor. The rewrite itself is audit-trailed, so recovery never
        happens silently. Clears the quarantine state on success.

        Returns:
            {"repaired": bool, "broken_at_seq": int|None,
             "repaired_at_seq": int|None, "record_count": int}
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_records ORDER BY seq ASC"
            ).fetchall()
        expected_prev = _GENESIS_HASH
        first_broken: Optional[int] = None
        for row in rows:
            payload = json.loads(row["payload"])
            recomputed = _compute_hash(
                row["prev_hash"], row["component"], row["event_type"],
                payload, row["timestamp"],
            )
            if row["prev_hash"] != expected_prev or recomputed != row["record_hash"]:
                first_broken = row["seq"]
                break
            expected_prev = row["record_hash"]
        if first_broken is None:
            self._set_meta("state", "active")
            return {"repaired": False, "reason": "chain already valid",
                    "broken_at_seq": None, "repaired_at_seq": None,
                    "record_count": len(rows)}
        # Rewrite the tail starting at first_broken, anchored at the last
        # verified record's hash (expected_prev at the point of break), and
        # append the auditable repair event IN THE SAME TRANSACTION — a crash
        # between rewrite and audit-log must never leave a silent repair.
        repaired_at_seq: Optional[int] = None
        with self._conn() as conn:
            tail = conn.execute(
                "SELECT * FROM audit_records WHERE seq >= ? ORDER BY seq ASC",
                (first_broken,),
            ).fetchall()
            prev = expected_prev
            for row in tail:
                payload = json.loads(row["payload"])
                new_hash = _compute_hash(
                    prev, row["component"], row["event_type"], payload, row["timestamp"]
                )
                conn.execute(
                    "UPDATE audit_records SET prev_hash=?, record_hash=? WHERE seq=?",
                    (prev, new_hash, row["seq"]),
                )
                prev = new_hash
            # Auditable repair event — recovery is never silent.
            event_ts = _now_iso()
            event_payload = {"broken_at_seq": first_broken, "anchor": expected_prev}
            event_hash = _compute_hash(
                prev, "chain", "repaired", event_payload, event_ts
            )
            cur = conn.execute(
                "INSERT INTO audit_records(component, event_type, payload, timestamp, prev_hash, record_hash)"
                " VALUES (?,?,?,?,?,?)",
                ("chain", "repaired", json.dumps(event_payload, ensure_ascii=False),
                 event_ts, prev, event_hash),
            )
            repaired_at_seq = cur.lastrowid
        self._set_meta("state", "active")
        self._set_meta("broken_at_seq", "")
        self._set_meta("repaired_at", _now_iso())
        return {"repaired": True, "broken_at_seq": first_broken,
                "repaired_at_seq": repaired_at_seq, "record_count": len(rows)}

    def get_chain(self, component: Optional[str] = None) -> List[AuditRecord]:
        with self._conn() as conn:
            if component:
                rows = conn.execute(
                    "SELECT * FROM audit_records WHERE component=? ORDER BY seq ASC", (component,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM audit_records ORDER BY seq ASC").fetchall()
        return [
            AuditRecord(
                seq=r["seq"], component=r["component"], event_type=r["event_type"],
                payload=json.loads(r["payload"]), timestamp=r["timestamp"],
                prev_hash=r["prev_hash"], record_hash=r["record_hash"],
            )
            for r in rows
        ]
