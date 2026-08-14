"""ApprovalQueue — restart-surviving queue for irreversible actions.

Nothing irreversible (stage 7 build, stage 8 combine, stage 9
promote-to-permanent, git commit, vault write) executes without an
explicit owner approval. Items persist in SQLite so the queue survives
restarts; every submit and decision is written to the UAC audit chain so
the gate history is never a black box.

Transitions are allowed only from PENDING — deciding an already-decided
item raises IdempotencyError (the guard cannot double-spend an approval).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.governance.db import default_db_path
from msb_v3.uac.audit_chain import AuditChainLike
from msb_v3.uac.chain_anchor import anchored_chain_from_env

# The blueprint's §0.6 approval-gated actions. The guard enforces these;
# an unknown kind is refused on submit (fail-closed — nothing sneaks
# through a kind the queue doesn't recognize).
APPROVAL_KINDS = ("build", "combine", "promote_knowledge", "git_commit", "vault_write")

STATUSES = ("PENDING", "APPROVED", "REJECTED", "CANCELLED")


class ApprovalError(Exception):
    """Base for approval-queue domain errors."""


class IdempotencyError(ApprovalError):
    """Item already decided — transitions are allowed only from PENDING."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ApprovalItem:
    item_id: str
    kind: str
    title: str
    payload: Dict[str, Any]
    evidence_refs: List[str]
    status: str = "PENDING"
    created_at: str = ""
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None
    reason: Optional[str] = None
    audit_failed: Optional[str] = None  # set on the deciding call if the audit write failed


class ApprovalQueue:
    def __init__(self, db_path: Optional[str] = None, audit_chain: Optional[AuditChainLike] = None) -> None:
        self.db_path = str(default_db_path() if db_path is None else db_path)
        self._audit = audit_chain if audit_chain is not None else anchored_chain_from_env()
        self._init_db()

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS approval_items ("
                " id TEXT PRIMARY KEY,"
                " kind TEXT NOT NULL,"
                " title TEXT NOT NULL,"
                " payload TEXT NOT NULL,"
                " evidence_refs TEXT NOT NULL,"
                " status TEXT NOT NULL,"
                " created_at TEXT NOT NULL,"
                " decided_at TEXT,"
                " decided_by TEXT,"
                " reason TEXT)"
            )

    def _row_to_item(self, row: sqlite3.Row) -> ApprovalItem:
        return ApprovalItem(
            item_id=row["id"],
            kind=row["kind"],
            title=row["title"],
            payload=json.loads(row["payload"]),
            evidence_refs=json.loads(row["evidence_refs"]),
            status=row["status"],
            created_at=row["created_at"],
            decided_at=row["decided_at"],
            decided_by=row["decided_by"],
            reason=row["reason"],
        )

    def _audit_append(self, event_type: str, payload: Dict[str, Any]) -> Optional[str]:
        """Append to the audit chain; on failure return the error instead of
        silently dropping it. The decision itself is already committed (the
        audit chain is a separate DB, so cross-DB atomicity is impossible) —
        surfacing the failure on the returned item/state keeps the "never a
        black box" contract honest: an un-audited decision is visible, not
        silent."""
        try:
            self._audit.append("approval", event_type, payload)
            return None
        except Exception as exc:
            return str(exc)

    def submit(
        self,
        kind: str,
        title: str,
        payload: Optional[Dict[str, Any]] = None,
        evidence_refs: Optional[List[str]] = None,
        item_id: Optional[str] = None,
    ) -> ApprovalItem:
        """Create a PENDING approval item; refuses unknown kinds."""
        if kind not in APPROVAL_KINDS:
            raise ValueError(f"unknown approval kind {kind!r}; allowed: {', '.join(APPROVAL_KINDS)}")
        iid = item_id or uuid.uuid4().hex[:12]
        created = _now_iso()
        payload = payload or {}
        refs = list(evidence_refs or [])
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO approval_items(id, kind, title, payload, evidence_refs, status, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (iid, kind, title, json.dumps(payload, ensure_ascii=False),
                 json.dumps(refs, ensure_ascii=False), "PENDING", created),
            )
        item = self.get(iid)
        if item is None:  # fail-closed: row was just inserted, must be readable
            raise ApprovalError(f"approval item {iid} not readable after create")
        item.audit_failed = self._audit_append(
            "submitted", {"item_id": iid, "kind": kind, "title": title}
        )
        return item

    def _decide(self, item_id: str, operator: str, status: str, reason: Optional[str]) -> ApprovalItem:
        # Atomic transition: the PENDING guard lives in the UPDATE's WHERE
        # clause, not in a separate read-then-write, so two concurrent
        # decisions cannot both succeed (the same race the audit chain's
        # BEGIN IMMEDIATE fix closed). rowcount == 0 means the item was
        # missing or already decided.
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE approval_items SET status=?, decided_at=?, decided_by=?, reason=?"
                " WHERE id=? AND status='PENDING'",
                (status, _now_iso(), operator, reason, item_id),
            )
            if cur.rowcount == 0:
                row = conn.execute(
                    "SELECT status FROM approval_items WHERE id=?", (item_id,)
                ).fetchone()
                if row is None:
                    raise ApprovalError(f"unknown approval item {item_id}")
                raise IdempotencyError(f"approval {item_id} already {row[0]}")
        item = self.get(item_id)
        if item is None:  # fail-closed: row was just updated, must be readable
            raise ApprovalError(f"approval item {item_id} not readable after decide")
        item.audit_failed = self._audit_append(
            status.lower(), {"item_id": item_id, "operator": operator, "reason": reason}
        )
        return item

    def approve(self, item_id: str, operator: str, reason: Optional[str] = None) -> ApprovalItem:
        return self._decide(item_id, operator, "APPROVED", reason)

    def reject(self, item_id: str, operator: str, reason: Optional[str] = None) -> ApprovalItem:
        if not reason:
            raise ApprovalError("reject requires a reason")
        return self._decide(item_id, operator, "REJECTED", reason)

    def cancel(self, item_id: str, operator: str, reason: Optional[str] = None) -> ApprovalItem:
        return self._decide(item_id, operator, "CANCELLED", reason)

    def get(self, item_id: str) -> Optional[ApprovalItem]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM approval_items WHERE id=?", (item_id,)).fetchone()
        return self._row_to_item(row) if row else None

    def pending(self) -> List[ApprovalItem]:
        return self.list(status="PENDING")

    def list(self, status: Optional[str] = None) -> List[ApprovalItem]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if status:
                rows = conn.execute(
                    "SELECT * FROM approval_items WHERE status=? ORDER BY created_at DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM approval_items ORDER BY created_at DESC").fetchall()
        return [self._row_to_item(r) for r in rows]
