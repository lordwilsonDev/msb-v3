"""Sovereign Node mutation approvals, isolated from the flywheel queue."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from msb_v3.uac.audit_chain import AuditChain


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NodeApprovalStore:
    def __init__(self, db_path: str, audit: AuditChain) -> None:
        self.db_path = str(db_path)
        self.audit = audit
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS node_approvals ("
                " id TEXT PRIMARY KEY, request_id TEXT NOT NULL, device_id TEXT NOT NULL,"
                " intent TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,"
                " decided_at TEXT, decided_by TEXT, reason TEXT)"
            )

    def submit(self, request_id: str, device_id: str, intent: Dict[str, Any]) -> str:
        approval_id = uuid.uuid4().hex
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO node_approvals(id, request_id, device_id, intent, status, created_at) VALUES(?,?,?,?,?,?)",
                (approval_id, request_id, device_id, json.dumps(intent, ensure_ascii=False), "PENDING", _now()),
            )
        self.audit.append("node", "approval.created", {"approval_id": approval_id, "request_id": request_id})
        return approval_id

    def get(self, approval_id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM node_approvals WHERE id=?", (approval_id,)).fetchone()
        return dict(row) if row else None

    def decide(self, approval_id: str, status: str, operator: str, reason: str = "") -> Dict[str, Any]:
        if status not in {"APPROVED", "REJECTED"}:
            raise ValueError("invalid node approval status")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE node_approvals SET status=?, decided_at=?, decided_by=?, reason=? WHERE id=? AND status='PENDING'",
                (status, _now(), operator, reason, approval_id),
            )
            if cur.rowcount != 1:
                raise ValueError("unknown or already decided node approval")
        item = self.get(approval_id)
        if item is None:
            raise ValueError("node approval disappeared after decision")
        self.audit.append("node", "approval.decided", {"approval_id": approval_id, "status": status, "operator": operator})
        return item
