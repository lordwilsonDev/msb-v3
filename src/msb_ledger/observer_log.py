"""Observer's Log — minimal human-readable narrative log for UAC.

Distinct from observability/metrics.py, which is Prometheus counters/gauges
(numeric telemetry) — confirmed to have no narrative logging capability
before this module was written (2026-08-02). The frozen UAC spec describes
Observer's Log as "runtime telemetry, human-readable chronology"; this
module covers the chronology half specifically, since metrics.py already
covers numeric telemetry and duplicating that would be redundant.

Deliberately minimal: append a plain-language line tied to a mission/session,
read them back in order. No delivery channels (Telegram/SNH from the
Sovereign Organism Completion Blueprint's fuller Observer's Log design) —
flagged as a future extension, not built now.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from msb_ledger.config import settings

_RUNTIME_ROOT = Path(settings.db_path).parent / "uac"
_OBSERVER_DB = _RUNTIME_ROOT / "observer_log.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS observer_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_observer_mission ON observer_entries(mission_id)")


@dataclass
class ObserverEntry:
    mission_id: str
    message: str
    timestamp: str


class ObserverLog:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = Path(db_path) if db_path else _OBSERVER_DB
        _init_db(self.db_path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def narrate(self, mission_id: str, message: str) -> ObserverEntry:
        """Append one plain-language line, e.g. 'Collecting regulations.'"""
        timestamp = _now_iso()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO observer_entries(mission_id, message, timestamp) VALUES (?,?,?)",
                (mission_id, message, timestamp),
            )
        return ObserverEntry(mission_id=mission_id, message=message, timestamp=timestamp)

    def read(self, mission_id: str) -> List[ObserverEntry]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT mission_id, message, timestamp FROM observer_entries WHERE mission_id=? ORDER BY id ASC",
                (mission_id,),
            ).fetchall()
        return [ObserverEntry(mission_id=r["mission_id"], message=r["message"], timestamp=r["timestamp"]) for r in rows]

    def read_as_text(self, mission_id: str) -> str:
        """Human-readable chronology, one line per entry — matches the blueprint's
        narration example format directly."""
        return "\n".join(f"[{e.timestamp}] {e.message}" for e in self.read(mission_id))
