"""Durable Vesta task lifecycle around the existing MSB runtime."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings
from msb_v3.vesta.models import ABind

TASK_STATES = (
    "RECEIVED",
    "AUTHENTICATED",
    "PLANNED",
    "AUTHORIZED",
    "WAITING_APPROVAL",
    "APPROVED",
    "EXECUTING",
    "VERIFYING",
    "RECOVERING",
    "COMPLETED",
    "DENIED",
    "QUARANTINED",
)

_ALLOWED_TRANSITIONS = {
    "RECEIVED": {"AUTHENTICATED", "DENIED", "QUARANTINED"},
    "AUTHENTICATED": {"PLANNED", "DENIED", "QUARANTINED"},
    "PLANNED": {"AUTHORIZED", "WAITING_APPROVAL", "DENIED", "QUARANTINED"},
    "AUTHORIZED": {"APPROVED", "EXECUTING", "DENIED", "QUARANTINED"},
    "WAITING_APPROVAL": {"APPROVED", "DENIED", "QUARANTINED"},
    "APPROVED": {"EXECUTING", "DENIED", "QUARANTINED"},
    "EXECUTING": {"VERIFYING", "RECOVERING", "QUARANTINED"},
    "VERIFYING": {"COMPLETED", "RECOVERING", "QUARANTINED"},
    "RECOVERING": {"COMPLETED", "QUARANTINED"},
    "COMPLETED": set(),
    "DENIED": set(),
    "QUARANTINED": set(),
}

_RECOVERABLE_STATES = {"EXECUTING", "VERIFYING", "RECOVERING"}


class TaskLifecycleError(ValueError):
    """Raised when a task attempts an invalid or stale state transition."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path(value: Optional[str]) -> Path:
    path = Path(value or settings.vesta_task_db_path)
    return path if path.is_absolute() else Path(settings.msb_home) / path


class VestaTaskStore:
    """SQLite task state with serialized transitions and restart-safe history."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = _db_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS vesta_tasks (
                    task_id TEXT PRIMARY KEY,
                    bind_id TEXT NOT NULL UNIQUE,
                    parent_task_id TEXT,
                    session_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    deadline TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error TEXT,
                    metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vesta_task_transitions (
                    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    reason TEXT,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES vesta_tasks(task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_vesta_task_state
                    ON vesta_tasks(state, updated_at);
                """
            )

    def create(self, bind: ABind, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        now = _now()
        payload = metadata or {}
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO vesta_tasks(
                        task_id, bind_id, parent_task_id, session_id, actor,
                        capabilities, policy_version, deadline, state,
                        created_at, updated_at, last_error, metadata
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        bind.task_id,
                        bind.bind_id,
                        bind.parent_task_id,
                        bind.session_id,
                        bind.actor,
                        json.dumps(list(bind.capabilities), sort_keys=True),
                        bind.policy_version,
                        bind.deadline,
                        "RECEIVED",
                        now,
                        now,
                        None,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO vesta_task_transitions(
                        task_id, from_state, to_state, reason, metadata, created_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (bind.task_id, None, "RECEIVED", "task created", "{}", now),
                )
            except sqlite3.IntegrityError as exc:
                raise TaskLifecycleError("task or bind already exists") from exc
        return self.get(bind.task_id)

    def transition(
        self,
        task_id: str,
        to_state: str,
        *,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if to_state not in TASK_STATES:
            raise TaskLifecycleError(f"unknown task state: {to_state}")
        now = _now()
        payload = metadata or {}
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state FROM vesta_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise TaskLifecycleError("unknown task")
            from_state = str(row["state"])
            if to_state not in _ALLOWED_TRANSITIONS[from_state]:
                raise TaskLifecycleError(f"invalid transition {from_state} -> {to_state}")
            last_error = reason if to_state in {"RECOVERING", "QUARANTINED"} else None
            conn.execute(
                "UPDATE vesta_tasks SET state=?, updated_at=?, last_error=? WHERE task_id=?",
                (to_state, now, last_error, task_id),
            )
            conn.execute(
                """
                INSERT INTO vesta_task_transitions(
                    task_id, from_state, to_state, reason, metadata, created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (task_id, from_state, to_state, reason, json.dumps(payload, ensure_ascii=False, sort_keys=True), now),
            )
        return self.get(task_id)

    def update_metadata(self, task_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT metadata FROM vesta_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise TaskLifecycleError("unknown task")
            metadata = json.loads(row["metadata"])
            metadata.update(patch)
            conn.execute(
                "UPDATE vesta_tasks SET metadata=?, updated_at=? WHERE task_id=?",
                (json.dumps(metadata, ensure_ascii=False, sort_keys=True), _now(), task_id),
            )
        return self.get(task_id)

    def recover_incomplete(self) -> List[Dict[str, Any]]:
        """Quarantine in-flight work discovered after a process restart."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT task_id FROM vesta_tasks WHERE state IN (?, ?, ?)",
                tuple(_RECOVERABLE_STATES),
            ).fetchall()
        recovered = []
        for row in rows:
            recovered.append(
                self.transition(
                    row["task_id"],
                    "QUARANTINED",
                    reason="in-flight task requires operator recovery after restart",
                )
            )
        return recovered

    def get(self, task_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM vesta_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise TaskLifecycleError("unknown task")
            transitions = conn.execute(
                "SELECT * FROM vesta_task_transitions WHERE task_id=? ORDER BY transition_id",
                (task_id,),
            ).fetchall()
        result: Dict[str, Any] = dict(row)
        result["capabilities"] = json.loads(result["capabilities"])
        result["metadata"] = json.loads(result["metadata"])
        result["transitions"] = [
            {
                "transition_id": item["transition_id"],
                "from_state": item["from_state"],
                "to_state": item["to_state"],
                "reason": item["reason"],
                "metadata": json.loads(item["metadata"]),
                "created_at": item["created_at"],
            }
            for item in transitions
        ]
        return result
