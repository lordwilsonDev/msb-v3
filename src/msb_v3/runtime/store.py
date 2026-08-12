"""Runtime store — queryable Task/Trace persistence for agent runs.

The hash-chained UAC audit chain is the authoritative event log (invariant I4:
every mutation is an append-only hash-chained event). This store is a
*derived projection* — a query convenience that lets operators answer "what
happened in run X?" without re-walking the chain. It is deliberately NOT an
alternative source of truth: if the store fails, the run continues and the
chain remains the record (fail-closed applies to governance/verification, not
to this convenience projection — see phase0-substrate-hardening.md).

DB location follows the audit-chain convention:
    Path(settings.db_path).parent / "runtime" / "runtime.db"
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.agent.dag import Task
from msb_v3.agent.executor import TaskResult
from msb_v3.agent.trace import AgentTrace
from msb_v3.core.config import settings

_RUNTIME_ROOT = Path(settings.db_path).parent / "runtime"
_DB = _RUNTIME_ROOT / "runtime.db"


def _init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS traces (
                run_id TEXT PRIMARY KEY,
                request TEXT NOT NULL,
                intent TEXT NOT NULL,
                graph_source TEXT NOT NULL,
                tasks TEXT NOT NULL,
                execution TEXT NOT NULL,
                verdict TEXT NOT NULL,
                outcome TEXT NOT NULL,
                created_ts TEXT NOT NULL,
                deterministic_hash TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                parent_id TEXT,
                goal TEXT NOT NULL,
                capabilities TEXT NOT NULL,
                tools TEXT NOT NULL,
                permissions TEXT NOT NULL,
                verification_method TEXT NOT NULL,
                timeout_s REAL NOT NULL,
                retry_policy TEXT NOT NULL,
                status TEXT NOT NULL,
                output TEXT NOT NULL,
                verification TEXT NOT NULL,
                error TEXT,
                latency_s REAL NOT NULL,
                attempts INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id, task_id)"
        )


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _un(value: str, default: Any = None) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class RuntimeStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = Path(db_path) if db_path else _DB
        _init_db(self.db_path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ---- writes ---------------------------------------------------------

    def save_trace(self, trace: AgentTrace) -> None:
        """Persist one trace row (best-effort caller)."""
        d = trace.as_dict()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO traces
                    (run_id, request, intent, graph_source, tasks, execution,
                     verdict, outcome, created_ts, deterministic_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    trace.run_id,
                    trace.request,
                    _j(d["intent"]),
                    d["graph_source"],
                    _j(d["tasks"]),
                    _j(d["execution"]),
                    d["verdict"],
                    _j(d["outcome"]),
                    d["created_ts"],
                    d["deterministic_hash"],
                ),
            )

    def save_task(self, run_id: str, task: Task, result: TaskResult, status: str) -> None:
        """Persist one per-task row after execution (best-effort caller)."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO tasks
                    (run_id, task_id, parent_id, goal, capabilities, tools,
                     permissions, verification_method, timeout_s, retry_policy,
                     status, output, verification, error, latency_s, attempts)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    task.task_id,
                    task.parent_id,
                    task.goal,
                    _j(list(task.required_capabilities)),
                    _j(list(task.tools)),
                    _j(list(task.permissions)),
                    task.verification_method,
                    task.timeout_s,
                    task.retry_policy,
                    status,
                    _j(result.output),
                    _j(result.verification),
                    result.error,
                    result.latency_s,
                    result.attempts,
                ),
            )

    # ---- reads ----------------------------------------------------------

    def get_trace(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM traces WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "request": row["request"],
            "intent": _un(row["intent"], {}),
            "graph_source": row["graph_source"],
            "tasks": _un(row["tasks"], []),
            "execution": _un(row["execution"], []),
            "verdict": row["verdict"],
            "outcome": _un(row["outcome"], {}),
            "created_ts": row["created_ts"],
            "deterministic_hash": row["deterministic_hash"],
        }

    def list_traces(self, limit: int = 25) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT run_id, request, verdict, deterministic_hash, created_ts "
                "FROM traces ORDER BY created_ts DESC LIMIT ?",
                (max(0, int(limit)),),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_tasks(self, run_id: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE run_id=? ORDER BY id ASC", (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_deterministic_hash(self, run_id: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT deterministic_hash FROM traces WHERE run_id=?", (run_id,)
            ).fetchone()
        return row["deterministic_hash"] if row else None
