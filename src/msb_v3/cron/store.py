"""Durable store for scheduled jobs and their run history.

Follows the runtime-store convention (``data/runtime/cron.db``, next to
runtime.db / tasks.db): SQLite with JSON columns. The store is the
*projection*; every execution also lands an evidence receipt on the
structured audit stream and a chain record on the UAC AuditChain (see
scheduler.py), mirroring the runtime store's philosophy (the chain is the
record; the store answers "what did the scheduler do" without re-walking
the chain).

Schema:

    cron_jobs(job_id PK, name, schedule, enabled, action_json,
              governance_json, created_at, updated_at)
    cron_runs(run_id PK, job_id, status, trigger, started_at, finished_at,
              duration_ms, attempt, summary_json, error)

Job statuses on cron_runs: RUNNING / SUCCESS / FAILED / BLOCKED / SKIPPED /
INTERRUPTED. A run is recovered (never silently dropped, never silently
resumed) on scheduler start: in-flight rows become INTERRUPTED.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings
from msb_v3.cron.parser import CronExpr

logger = logging.getLogger(__name__)

RUN_STATUSES = frozenset({"RUNNING", "SUCCESS", "FAILED", "BLOCKED", "SKIPPED", "INTERRUPTED"})
TERMINAL_STATUSES = frozenset({"SUCCESS", "FAILED", "BLOCKED", "SKIPPED", "INTERRUPTED"})


def default_db_path() -> Path:
    """data/runtime/cron.db, derived from settings.db_path so MSB_DB_PATH
    moves the cron store together with the rest of the data dir."""
    return Path(settings.cron_db_path) if settings.cron_db_path else Path(settings.db_path).parent / "runtime" / "cron.db"


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
            CREATE TABLE IF NOT EXISTS cron_jobs (
                job_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                schedule TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                action_json TEXT NOT NULL,
                governance_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cron_runs (
                run_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                status TEXT NOT NULL,
                trigger TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_ms REAL,
                attempt INTEGER NOT NULL,
                summary_json TEXT NOT NULL,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_cron_runs_job ON cron_runs(job_id, started_at);
            """
        )


class CronStore:
    """Durable job definitions + run history (derived projection)."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = Path(db_path) if db_path else default_db_path()
        _init_db(self.db_path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # --- jobs ------------------------------------------------------------

    def create_job(
        self,
        job_id: str,
        name: str,
        schedule: str,
        action: Dict[str, Any],
        *,
        enabled: bool = True,
        governance: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist a job. The schedule is validated here so a bad expression
        can never be stored (fail-closed at the boundary)."""
        CronExpr.parse(schedule)  # raises ValueError -> 422/CLI error
        if not job_id.strip():
            raise ValueError("job_id is required")
        if not name.strip():
            raise ValueError("name is required")
        now = _now()
        gov = {
            "requires_approval": bool((governance or {}).get("requires_approval", False)),
            "max_retries": int((governance or {}).get("max_retries", 2)),
            "timeout_s": float((governance or {}).get("timeout_s", 300.0)),
            "notify_on_failure": bool((governance or {}).get("notify_on_failure", False)),
        }
        row: Dict[str, Any] = {
            "job_id": job_id.strip(),
            "name": name.strip(),
            "schedule": schedule.strip(),
            "enabled": enabled,
            "action": action,
            "governance": gov,
            "created_at": now,
            "updated_at": now,
        }
        with self._conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO cron_jobs(job_id, name, schedule, enabled, action_json, governance_json, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (
                        row["job_id"],
                        row["name"],
                        row["schedule"],
                        int(row["enabled"]),
                        _j(row["action"]),
                        _j(row["governance"]),
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"job already exists: {row['job_id']}") from exc
        return row

    def get_job(self, job_id: str) -> Dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM cron_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown cron job: {job_id}")
        return self._row_to_job(row)

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM cron_jobs ORDER BY job_id").fetchall()
        return [self._row_to_job(r) for r in rows]

    def update_job(self, job_id: str, **fields: Any) -> Dict[str, Any]:
        """Partial update (schedule/enabled/action/governance/name)."""
        job = self.get_job(job_id)
        if "schedule" in fields and fields["schedule"] != job["schedule"]:
            CronExpr.parse(str(fields["schedule"]))
        if "action" in fields:
            fields["action"] = fields["action"] if isinstance(fields["action"], dict) else job["action"]
        if "governance" in fields and isinstance(fields["governance"], dict):
            merged = {**job["governance"], **fields["governance"]}
            fields["governance"] = {
                "requires_approval": bool(merged.get("requires_approval", False)),
                "max_retries": int(merged.get("max_retries", 2)),
                "timeout_s": float(merged.get("timeout_s", 300.0)),
                "notify_on_failure": bool(merged.get("notify_on_failure", False)),
            }
        with self._conn() as conn:
            sets, vals = [], []
            for key in ("name", "schedule", "enabled", "action", "governance"):
                if key in fields:
                    col = key if key in ("name", "schedule", "enabled") else f"{key}_json"
                    sets.append(f"{col}=?")
                    val = fields[key]
                    if key == "enabled":
                        val = int(bool(val))
                    elif key in ("action", "governance"):
                        val = _j(val)
                    vals.append(val)
            if not sets:
                return job
            vals.append(_now())
            vals.append(job_id)
            conn.execute(f"UPDATE cron_jobs SET {', '.join(sets)}, updated_at=? WHERE job_id=?", vals)
        return self.get_job(job_id)

    def delete_job(self, job_id: str) -> None:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM cron_jobs WHERE job_id=?", (job_id,))
        if cur.rowcount == 0:
            raise KeyError(f"unknown cron job: {job_id}")

    def _row_to_job(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "name": row["name"],
            "schedule": row["schedule"],
            "enabled": bool(row["enabled"]),
            "action": _un(row["action_json"], {}),
            "governance": _un(row["governance_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # --- runs ------------------------------------------------------------

    def start_run(self, job_id: str, trigger: str, attempt: int = 1) -> str:
        """Open a run row; returns the run_id."""
        run_id = f"cron-{uuid.uuid4().hex[:12]}"
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO cron_runs(run_id, job_id, status, trigger, started_at, attempt, summary_json)"
                " VALUES (?,?,?,?,?,?,?)",
                (run_id, job_id, "RUNNING", trigger, _now(), attempt, _j({})),
            )
        return run_id

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        summary: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        if status not in RUN_STATUSES:
            raise ValueError(f"invalid run status: {status}")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT started_at FROM cron_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            conn.execute(
                "UPDATE cron_runs SET status=?, finished_at=?, duration_ms=?, summary_json=?, error=? WHERE run_id=?",
                (status, _now(), _duration_ms(row["started_at"] if row else None), _j(summary or {}), error, run_id),
            )

    def is_running(self, job_id: str) -> bool:
        """Overlap guard: a job with an in-flight RUNNING row is never
        started a second time (schedule or manual)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM cron_runs WHERE job_id=? AND status='RUNNING' LIMIT 1",
                (job_id,),
            ).fetchone()
        return row is not None

    def history(self, job_id: str, limit: int = 25) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM cron_runs WHERE job_id=? ORDER BY started_at DESC LIMIT ?",
                (job_id, max(0, int(limit))),
            ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def list_runs(self, limit: int = 25) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM cron_runs ORDER BY started_at DESC LIMIT ?",
                (max(0, int(limit)),),
            ).fetchall()
        return [self._row_to_run(r) for r in rows]

    def _row_to_run(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "job_id": row["job_id"],
            "status": row["status"],
            "trigger": row["trigger"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "duration_ms": row["duration_ms"],
            "attempt": row["attempt"],
            "summary": _un(row["summary_json"], {}),
            "error": row["error"],
        }

    def recover_inflight(self) -> List[Dict[str, Any]]:
        """After a restart, in-flight RUNNING rows are marked INTERRUPTED —
        never silently resumed (the schedule may have drifted), never
        silently dropped (the run row stays for the history)."""
        with self._conn() as conn:
            rows = conn.execute("SELECT run_id, job_id FROM cron_runs WHERE status='RUNNING'").fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE cron_runs SET status='INTERRUPTED', finished_at=?, error=? WHERE run_id=?",
                    (_now(), "interrupted by scheduler restart", row["run_id"]),
                )
        return [dict(r) for r in rows]

    def prune_history(self, job_id: str, keep: int) -> int:
        """Keep the newest ``keep`` run rows per job (older rows deleted).
        Returns the number of rows removed."""
        if keep <= 0:
            return 0
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT run_id FROM cron_runs WHERE job_id=? ORDER BY started_at DESC LIMIT -1 OFFSET ?",
                (job_id, keep),
            ).fetchall()
            for row in rows:
                conn.execute("DELETE FROM cron_runs WHERE run_id=?", (row["run_id"],))
        return len(rows)


def _duration_ms(started_at: Optional[str]) -> Optional[float]:
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.now(timezone.utc)
        return (end - start).total_seconds() * 1000.0
    except ValueError:
        return None
