"""Memory store — SQLite-backed message history with truncation.

.. deprecated:: 0.3.2
    This module is the legacy memory store. Use ``msb_v3.memory_fabric``
    (primary, with provenance/verification/consolidation) instead.
    This store will be removed in a future version.
"""

from __future__ import annotations

import sqlite3
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from msb_v3.core.config import settings


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    ts: float = field(default_factory=time.time)
    tokens: int = 0


class MemoryStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        warnings.warn(
            "MemoryStore is deprecated — use msb_v3.memory_fabric.store instead",
            DeprecationWarning,
            stacklevel=2,
        )
        self.db_path = Path(db_path or settings.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    ts REAL NOT NULL,
                    tokens INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_session_ts ON messages(session, ts)"
            )

    def append(self, session: str, message: Message) -> int | None:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO messages(session, role, content, ts, tokens) VALUES (?,?,?,?,?)",
                (session, message.role, message.content, message.ts, message.tokens),
            )
            return cur.lastrowid

    def recent(self, session: str, limit: int = 50) -> List[Message]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT role, content, ts, tokens FROM messages WHERE session=? ORDER BY ts DESC LIMIT ?",
                (session, limit),
            ).fetchall()
        return [Message(role=r["role"], content=r["content"], ts=r["ts"], tokens=r["tokens"]) for r in rows]

    def truncate(self, session: str, max_tokens: int = 4000) -> List[Message]:
        msgs = self.recent(session)
        kept: List[Message] = []
        total = 0
        for m in reversed(msgs):
            total += m.tokens or len(m.content.split())
            if total > max_tokens:
                break
            kept.append(m)
        return kept

    def clear(self, session: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM messages WHERE session=?", (session,))
