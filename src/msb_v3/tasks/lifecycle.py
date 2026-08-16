"""Event-sourced task lifecycle (unified-architecture §28).

``TaskLifecycle`` is the durable home of the unified task object:

    create(task)                  -> TASK_CREATED, state CREATED
    transition(task_id, state)    -> canonical event (PLAN_CREATED, ...)
                                     + state-machine-validated move
    emit(task_id, event, payload) -> informational events (TOOL_EXECUTED,
                                     VERIFICATION_PASSED, ...), no state change

Every event is mirrored to the UAC AuditChain (component="tasks",
event_type="task.<EVENT>") — the chain is the *authoritative sequence*
(§28). The sqlite store is the derived projection: same philosophy as
runtime/store.py (chain is the record; the store answers "what happened in
run X?" without re-walking the chain). A chain append failure is logged and
never breaks the run.

``EventingProvider`` wraps any ToolProvider so tool-level events
(TOOL_REQUESTED / TOOL_EXECUTED / MUTATION_COMMITTED / POLICY_CHECKED) flow
into the lifecycle for the current task.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from msb_v3.core.config import settings
from msb_v3.tasks.events import (
    RECOVERABLE_STATES,
    TASK_EVENTS,
    TaskLifecycleError,
    event_for_state,
    validate_transition,
)
from msb_v3.tasks.models import UnifiedTask
from msb_v3.uac.audit_chain import AuditChainLike
from msb_v3.uac.chain_anchor import anchored_chain_from_env

logger = logging.getLogger(__name__)

# Follows the runtime-store convention (Path(settings.db_path).parent /
# "runtime"), so the task projection sits beside the trace projection.
_DB = Path(settings.db_path).parent / "runtime" / "tasks.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _un(value: str, default: Any = None) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS unified_tasks (
                task_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                state TEXT,
                audit_seq INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_task_events_task
                ON task_events(task_id, event_id);
            """
        )


class TaskLifecycle:
    """Durable task object + event log, mirrored to the authoritative chain."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        chain: Optional[AuditChainLike] = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path else _DB
        self._chain = chain  # AuditChainLike; lazily resolved if None
        _init_db(self.db_path)

    # -- internals --------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _chain_ref(self) -> AuditChainLike:
        if self._chain is None:
            self._chain = anchored_chain_from_env()
        return self._chain

    def _row(self, conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM unified_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise TaskLifecycleError(f"unknown task: {task_id}")
        return row

    def _record_event(
        self,
        conn: sqlite3.Connection,
        task_id: str,
        event_type: str,
        payload: Dict[str, Any],
        state: Optional[str],
    ) -> Dict[str, Any]:
        """Append one event row + mirror to the chain. Returns event metadata
        (audit_seq may be None if the chain append failed — logged, never
        fatal: the store is a projection, the chain the record)."""
        audit_seq: Optional[int] = None
        try:
            record = self._chain_ref().append(
                "tasks", f"task.{event_type}", {"task_id": task_id, **payload}
            )
            audit_seq = record.seq
        except Exception as exc:  # noqa: BLE001 — projection must not break the run
            logger.warning("task event %s for %s not mirrored to chain: %s", event_type, task_id, exc)
        now = _now()
        cur = conn.execute(
            "INSERT INTO task_events(task_id, event_type, payload, state, audit_seq, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (task_id, event_type, _j(payload), state, audit_seq, now),
        )
        return {
            "event_id": cur.lastrowid,
            "task_id": task_id,
            "event_type": event_type,
            "payload": payload,
            "state": state,
            "audit_seq": audit_seq,
            "created_at": now,
        }

    # -- writes -----------------------------------------------------------

    def create(self, task: UnifiedTask) -> Dict[str, Any]:
        """Persist the task (state CREATED) and emit TASK_CREATED."""
        now = _now()
        task.state = "CREATED"
        task.created_at = task.updated_at = now
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO unified_tasks(task_id, state, created_at, updated_at, body)"
                " VALUES (?,?,?,?,?)",
                (task.task_id, task.state, task.created_at, task.updated_at, _j(task.as_dict())),
            )
            self._record_event(
                conn, task.task_id, "TASK_CREATED",
                {"kind": task.kind, "tenant": task.tenant, "session": task.session, "source": task.source},
                task.state,
            )
        return self.get(task.task_id)

    def emit(
        self,
        task_id: str,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        state: Optional[str] = None,
        update: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Emit one event. ``state`` optionally moves the state machine;
        ``update`` merges §27 sections into the stored body."""
        if event_type not in TASK_EVENTS:
            raise TaskLifecycleError(f"unknown task event: {event_type}")
        payload = payload or {}
        now = _now()
        with self._connect() as conn:
            row = self._row(conn, task_id)
            current_state = str(row["state"])
            if state is not None:
                validate_transition(current_state, state)
                current_state = state
            body = _un(row["body"], {})
            if update:
                body.update(update)
            body["state"] = current_state
            body["updated_at"] = now
            event = self._record_event(conn, task_id, event_type, payload, current_state)
            conn.execute(
                "UPDATE unified_tasks SET state=?, updated_at=?, body=? WHERE task_id=?",
                (current_state, now, _j(body), task_id),
            )
        return event

    def transition(
        self,
        task_id: str,
        to_state: str,
        *,
        reason: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Move the state machine; emits the canonical §28 event for the state."""
        return self.emit(
            task_id,
            event_for_state(to_state),
            {**(payload or {}), "reason": reason} if reason else (payload or {}),
            state=to_state,
        )

    def update(self, task_id: str, sections: Dict[str, Any]) -> Dict[str, Any]:
        """Merge §27 sections into the stored body (no event)."""
        now = _now()
        with self._connect() as conn:
            row = self._row(conn, task_id)
            body = _un(row["body"], {})
            body.update(sections)
            body["updated_at"] = now
            conn.execute(
                "UPDATE unified_tasks SET updated_at=?, body=? WHERE task_id=?",
                (now, _j(body), task_id),
            )
        return self.get(task_id)

    def append_audit_ref(self, task_id: str, seq: int, event_type: str) -> Dict[str, Any]:
        """Record a chain reference on the task (§27 audit section)."""
        return self.update(task_id, {"audit": self.get(task_id)["task"].get("audit", []) + [{"seq": seq, "event_type": event_type}]})

    def recover_incomplete(self) -> List[Dict[str, Any]]:
        """Quarantine in-flight tasks discovered after a restart (never
        silently resumed, never silently dropped)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT task_id FROM unified_tasks WHERE state IN (%s)"
                % ",".join("?" * len(RECOVERABLE_STATES)),
                tuple(RECOVERABLE_STATES),
            ).fetchall()
        recovered = []
        for row in rows:
            recovered.append(
                self.transition(
                    row["task_id"],
                    "QUARANTINED",
                    reason="in-flight task quarantined after process restart",
                )
            )
        return recovered

    # -- reads ------------------------------------------------------------

    def get(self, task_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = self._row(conn, task_id)
            events = conn.execute(
                "SELECT event_id, event_type, payload, state, audit_seq, created_at"
                " FROM task_events WHERE task_id=? ORDER BY event_id",
                (task_id,),
            ).fetchall()
        return {
            "task": _un(row["body"], {}),
            "state": row["state"],
            "events": [
                {
                    "event_id": e["event_id"],
                    "event_type": e["event_type"],
                    "payload": _un(e["payload"], {}),
                    "state": e["state"],
                    "audit_seq": e["audit_seq"],
                    "created_at": e["created_at"],
                }
                for e in events
            ],
        }

    def events(self, task_id: str) -> List[Dict[str, Any]]:
        return self.get(task_id)["events"]

    def list(self, limit: int = 25) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT task_id, state, created_at, updated_at FROM unified_tasks"
                " ORDER BY created_at DESC LIMIT ?",
                (max(0, int(limit)),),
            ).fetchall()
        return [dict(r) for r in rows]


class EventingProvider:
    """ToolProvider wrapper that feeds tool-level events into the lifecycle.

    Wraps any ToolProvider (SafeProvider, BridgeProvider, ...) and emits
    TOOL_REQUESTED / TOOL_EXECUTED / MUTATION_COMMITTED / POLICY_CHECKED per
    call for the current task — the task-granularity event source that makes
    "what did this task actually do" replayable from the chain.
    """

    def __init__(
        self,
        provider: Any,
        lifecycle: Optional[TaskLifecycle] = None,
        task_id: Optional[str] = None,
        *,
        emit: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self._provider = provider
        self._lifecycle = lifecycle
        self._task_id = task_id
        self._emit = emit  # injectable for tests (keeps the wrapper pure)

    def _event(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self._emit is not None:
            self._emit(event_type, payload)
            return
        if self._lifecycle is None or self._task_id is None:
            return
        try:
            self._lifecycle.emit(self._task_id, event_type, payload)
        except Exception as exc:  # noqa: BLE001 — events must never break the run
            logger.debug("lifecycle event %s failed: %s", event_type, exc)

    async def run_tool(self, name: str, *, task: Any, inputs: Dict[str, Any], session: str) -> Any:
        self._event("TOOL_REQUESTED", {"tool": name})
        try:
            result = await self._provider.run_tool(name, task=task, inputs=inputs, session=session)
        except Exception as exc:
            self._event("POLICY_CHECKED", {"tool": name, "decision": "DENIED", "reason": str(exc)})
            raise
        self._event("TOOL_EXECUTED", {"tool": name, "ok": True})
        if name == "vault_write":
            self._event("MUTATION_COMMITTED", {"tool": name})
        return result
