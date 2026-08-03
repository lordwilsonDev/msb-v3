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
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings

_RUNTIME_ROOT = Path(settings.db_path).parent / "uac"
_AUDIT_DB = _RUNTIME_ROOT / "audit_chain.db"
_GENESIS_HASH = "0" * 64


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


class AuditChain:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = Path(db_path) if db_path else _AUDIT_DB
        _init_db(self.db_path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _last_hash(self, conn: sqlite3.Connection) -> str:
        row = conn.execute("SELECT record_hash FROM audit_records ORDER BY seq DESC LIMIT 1").fetchone()
        return row["record_hash"] if row else _GENESIS_HASH

    def append(self, component: str, event_type: str, payload: Dict[str, Any]) -> AuditRecord:
        timestamp = _now_iso()
        with self._conn() as conn:
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
