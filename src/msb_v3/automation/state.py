"""Runtime state for *living* automations (data/runtime/automation/state.db).

The manifest (JSONL) is the immutable creation ledger; this SQLite store is
the mutable contract — which automation is enabled, on what schedule, and
what its last run did. Together they make the manifest IS the automation:
appending a ledger entry with ``schedule``+``action`` creates a living
automation, flipping ``enabled`` here disables it, and the dispatcher ticks
it on the wake cycle. Same runtime-store convention as cron/wake.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)


def default_state_path() -> Path:
    if settings.automation_manifest_path:
        base = Path(settings.automation_manifest_path).parent
    else:
        base = Path(settings.db_path).parent / "runtime" / "automation"
    return base / "state.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS automation_state (
                automation_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                schedule TEXT,
                next_run TEXT,
                last_run_ts TEXT,
                last_run_status TEXT,
                last_run_summary TEXT
            );
            """
        )


class AutomationState:
    """Mutable live-contract store for managed automations."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = Path(db_path) if db_path else default_state_path()
        _init_db(self.db_path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def upsert(self, automation_id: str, schedule: str, enabled: bool = True) -> Dict[str, Any]:
        """Register/refresh a managed automation's contract."""
        schedule = (schedule or "").strip()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO automation_state(automation_id, enabled, schedule, next_run)"
                " VALUES (?,?,?,?)"
                " ON CONFLICT(automation_id) DO UPDATE SET schedule=excluded.schedule, enabled=excluded.enabled",
                (automation_id, int(enabled), schedule, _next_from_schedule(schedule)),
            )
        return self.get(automation_id)

    def get(self, automation_id: str) -> Dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM automation_state WHERE automation_id=?", (automation_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown automation state: {automation_id}")
        return self._row(row)

    def set_enabled(self, automation_id: str, enabled: bool) -> Dict[str, Any]:
        with self._conn() as conn:
            conn.execute("UPDATE automation_state SET enabled=? WHERE automation_id=?", (int(enabled), automation_id))
        return self.get(automation_id)

    def mark_run(self, automation_id: str, status: str, summary: str) -> None:
        """Record a run and advance next_run from the schedule."""
        with self._conn() as conn:
            row = conn.execute("SELECT schedule FROM automation_state WHERE automation_id=?", (automation_id,)).fetchone()
            schedule = row["schedule"] if row else None
            conn.execute(
                "UPDATE automation_state SET last_run_ts=?, last_run_status=?, last_run_summary=?, next_run=? WHERE automation_id=?",
                (_now(), status, (summary or "")[:300], _next_from_schedule(schedule), automation_id),
            )

    def list(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM automation_state ORDER BY automation_id").fetchall()
        return [self._row(r) for r in rows]

    def due(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Enabled automations whose next_run has arrived (or has no next_run
        yet but a schedule — first-run grace). Deterministic order by id."""
        now = now or datetime.now(timezone.utc)
        now_s = now.isoformat()
        out: List[Dict[str, Any]] = []
        for row in self.list():
            if not row["enabled"] or not row["schedule"]:
                continue
            nxt = row["next_run"]
            if nxt is None or nxt <= now_s:
                out.append(row)
        return out

    def _row(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "automation_id": row["automation_id"],
            "enabled": bool(row["enabled"]),
            "schedule": row["schedule"],
            "next_run": row["next_run"],
            "last_run_ts": row["last_run_ts"],
            "last_run_status": row["last_run_status"],
            "last_run_summary": row["last_run_summary"],
        }


def _next_from_schedule(schedule: Optional[str]) -> Optional[str]:
    """Next run time from a 5-field cron expression (UTC), or None when the
    schedule is empty/invalid (a bad schedule disables auto-advance, never
    crashes the tick)."""
    if not schedule:
        return None
    try:
        from msb_v3.cron.parser import CronExpr

        nxt = CronExpr.parse(schedule).next_run()
        return nxt.isoformat() if nxt else None
    except ValueError:
        logger.warning("invalid automation schedule %r", schedule)
        return None
