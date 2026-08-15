"""Durable Vesta approvals for exact file-write contracts."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from msb_v3.core.config import settings


class ApprovalError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path(value: Optional[str]) -> Path:
    path = Path(value or settings.vesta_task_db_path)
    return path if path.is_absolute() else Path(settings.msb_home) / path


class VestaApprovalStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = _db_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vesta_approvals (
                    approval_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    bind_id TEXT NOT NULL UNIQUE,
                    target_path TEXT NOT NULL,
                    payload_evidence_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    expected_sha256 TEXT,
                    policy_version TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    reason TEXT
                )
                """
            )

    def submit(
        self,
        task_id: str,
        bind_id: str,
        target_path: str,
        payload_evidence_id: str,
        payload_sha256: str,
        expected_sha256: Optional[str],
        policy_version: str,
        expires_at: str,
    ) -> Dict[str, Any]:
        approval_id = f"ack_{uuid.uuid4().hex}"
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO vesta_approvals(
                        approval_id, task_id, bind_id, target_path,
                        payload_evidence_id, payload_sha256, expected_sha256,
                        policy_version, expires_at, status, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        approval_id,
                        task_id,
                        bind_id,
                        target_path,
                        payload_evidence_id,
                        payload_sha256,
                        expected_sha256,
                        policy_version,
                        expires_at,
                        "PENDING",
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ApprovalError("approval already exists for this bind") from exc
        return self.get(approval_id)

    def get(self, approval_id: str) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM vesta_approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if row is None:
            raise ApprovalError("unknown approval")
        return dict(row)

    def list(self, status: Optional[str] = None) -> list[Dict[str, Any]]:
        """All write approvals, newest last; filter to one status (e.g. PENDING)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if status:
                rows = conn.execute(
                    "SELECT * FROM vesta_approvals WHERE status=? ORDER BY created_at",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM vesta_approvals ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    def approve(self, approval_id: str, operator: str) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        expired = False
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, expires_at FROM vesta_approvals WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            if row is None:
                raise ApprovalError("unknown approval")
            if row[0] != "PENDING":
                raise ApprovalError("approval is already decided")
            try:
                expires = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ApprovalError("approval has invalid expiration") from exc
            if now >= expires:
                conn.execute(
                    "UPDATE vesta_approvals SET status='EXPIRED', decided_at=?, reason=? WHERE approval_id=?",
                    (_now(), "approval expired", approval_id),
                )
                expired = True
            else:
                conn.execute(
                    "UPDATE vesta_approvals SET status='APPROVED', decided_at=?, decided_by=? WHERE approval_id=? AND status='PENDING'",
                    (_now(), operator, approval_id),
                )
        if expired:
            raise ApprovalError("approval expired")
        return self.get(approval_id)

    def reject(self, approval_id: str, operator: str, reason: str = "") -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                "UPDATE vesta_approvals SET status='REJECTED', decided_at=?, decided_by=?, reason=? WHERE approval_id=? AND status='PENDING'",
                (_now(), operator, reason, approval_id),
            )
            if updated.rowcount != 1:
                raise ApprovalError("unknown or already decided approval")
        return self.get(approval_id)

    def void(self, approval_id: str, reason: str = "") -> Dict[str, Any]:
        """Mark an APPROVED approval VOID because execution never completed
        validly (kill switch, precondition/postcondition failure, quarantine).

        VOID is terminal: ``approve`` refuses anything that is not PENDING and
        ``reject`` only touches PENDING rows, so a voided approval can never
        be re-decided into an execution.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                "UPDATE vesta_approvals SET status='VOID', decided_at=?, reason=? WHERE approval_id=? AND status='APPROVED'",
                (_now(), reason, approval_id),
            )
            if updated.rowcount != 1:
                raise ApprovalError("approval is not in an APPROVED state")
        return self.get(approval_id)
