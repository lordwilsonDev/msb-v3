"""Durable wake inbox/outbox store (data/runtime/wake.db).

Follows the runtime-store convention (SQLite with JSON columns, next to
cron.db / runtime.db / tasks.db): the inbox holds messages left from any
session, the outbox holds the resident agent's responses. Statuses on
wake_inbox: pending / done / failed — a failed message keeps its error so
the operator sees why the resident agent could not answer, and the next
cycle only ever picks up ``pending`` rows (never retried automatically —
the operator re-posts if it matters).

Schema:

    wake_inbox(id PK, ts, sender, text, status, response_id, error,
               responded_at)
    wake_outbox(id PK, ts, in_reply_to, text, source)
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)

INBOX_STATUSES = frozenset({"pending", "done", "failed"})


def default_db_path() -> Path:
    """data/runtime/wake.db, derived from settings.db_path so MSB_DB_PATH
    moves the wake store together with the rest of the data dir."""
    if settings.wake_db_path:
        return Path(settings.wake_db_path)
    return Path(settings.db_path).parent / "runtime" / "wake.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS wake_inbox (
                id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                sender TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL,
                response_id TEXT,
                error TEXT,
                responded_at TEXT
            );
            CREATE TABLE IF NOT EXISTS wake_outbox (
                id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                in_reply_to TEXT,
                text TEXT NOT NULL,
                source TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_wake_inbox_status ON wake_inbox(status, ts);
            """
        )


class WakeStore:
    """Durable inbox/outbox for the resident wake agent."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = Path(db_path) if db_path else default_db_path()
        _init_db(self.db_path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # --- inbox -----------------------------------------------------------

    def post(self, text: str, sender: str = "operator") -> Dict[str, Any]:
        """Drop a message into the inbox. Returns the stored row."""
        text = (text or "").strip()
        if not text:
            raise ValueError("wake message text is required")
        row: Dict[str, Any] = {
            "id": f"wake-{uuid.uuid4().hex[:12]}",
            "ts": _now(),
            "sender": (sender or "operator").strip()[:80] or "operator",
            "text": text[:4000],
            "status": "pending",
            "response_id": None,
            "error": None,
            "responded_at": None,
        }
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO wake_inbox(id, ts, sender, text, status, response_id, error, responded_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (row["id"], row["ts"], row["sender"], row["text"], row["status"], None, None, None),
            )
        return row

    def pending(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Oldest ``pending`` messages, in order (bounded by limit)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM wake_inbox WHERE status='pending' ORDER BY ts ASC LIMIT ?",
                (max(0, int(limit)),),
            ).fetchall()
        return [self._row_to_inbox(r) for r in rows]

    def pending_count(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM wake_inbox WHERE status='pending'").fetchone()
        return int(row["c"]) if row else 0

    def get_inbox(self, msg_id: str) -> Dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM wake_inbox WHERE id=?", (msg_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown wake message: {msg_id}")
        return self._row_to_inbox(row)

    def respond(self, msg_id: str, text: str, source: str = "wake") -> Dict[str, Any]:
        """Write an outbox reply and mark the inbox message done."""
        out_id = f"wake-{uuid.uuid4().hex[:12]}"
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM wake_inbox WHERE id=?", (msg_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown wake message: {msg_id}")
            conn.execute(
                "INSERT INTO wake_outbox(id, ts, in_reply_to, text, source) VALUES (?,?,?,?,?)",
                (out_id, _now(), msg_id, (text or "")[:8000], source),
            )
            conn.execute(
                "UPDATE wake_inbox SET status='done', response_id=?, responded_at=? WHERE id=?",
                (out_id, _now(), msg_id),
            )
        return {"id": out_id, "in_reply_to": msg_id, "text": (text or "")[:8000], "source": source}

    def mark_failed(self, msg_id: str, error: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE wake_inbox SET status='failed', error=?, responded_at=? WHERE id=?",
                ((error or "")[:500], _now(), msg_id),
            )

    def _row_to_inbox(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "ts": row["ts"],
            "sender": row["sender"],
            "text": row["text"],
            "status": row["status"],
            "response_id": row["response_id"],
            "error": row["error"],
            "responded_at": row["responded_at"],
        }

    # --- outbox ----------------------------------------------------------

    def notify(self, text: str, source: str = "audit") -> Dict[str, Any]:
        """Post a notice straight to the outbox (no inbox message, no agent
        turn) — used by the self-maintenance audit so findings reach the
        operator without the brain talking to itself."""
        text = (text or "").strip()
        if not text:
            raise ValueError("notice text is required")
        out_id = f"wake-{uuid.uuid4().hex[:12]}"
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO wake_outbox(id, ts, in_reply_to, text, source) VALUES (?,?,?,?,?)",
                (out_id, _now(), None, text[:8000], source),
            )
        return {"id": out_id, "in_reply_to": None, "text": text[:8000], "source": source}

    def outbox(self, limit: int = 25) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM wake_outbox ORDER BY ts DESC LIMIT ?",
                (max(0, int(limit)),),
            ).fetchall()
        return [self._row_to_outbox(r) for r in rows]

    def _row_to_outbox(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "ts": row["ts"],
            "in_reply_to": row["in_reply_to"],
            "text": row["text"],
            "source": row["source"],
        }
