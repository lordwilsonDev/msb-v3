"""Memory store — SQLite-backed message history with truncation.

Not a duplicate of ``msb_v3.memory_fabric`` (checked 2026-09-12, when this
module's deprecation warning turned out to be premature): ``MemoryItem`` has
no ``session`` or ``role`` field, and the Fabric's only read paths
(``list_active`` — importance/recency ranked, ``search_keywords``/
``search_embedding`` — relevance ranked) can't reproduce "the last N
messages of session X, in order," which is the one thing ``/chat`` needs
turn-to-turn. Forcing that through the Fabric would mean fabricating an
``importance``/``verification_state`` for every raw chat turn, and risking
``consolidate()`` merging conversation turns that happen to share tags.

The two stores serve different domains and both stay: this one is the
session-scoped recency window ``/chat`` reads and writes every turn; each
exchange is *also* recorded as an EPISODIC memory in the Fabric (see
``api/chat.py``) for durable, cross-session, relevance-ranked recall — a
job this store was never designed for and shouldn't try to do.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from msb_v3.core.config import settings
from msb_v3.secrets.redact import redact


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    ts: float = field(default_factory=time.time)
    tokens: int = 0


class MemoryStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
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
        # The *memory* channel, redacted on write: whatever lands here is
        # replayed into prompts by recent()/truncate(), so a secret written once
        # would keep re-entering model context on every later turn. Redacting at
        # the write means it is never stored, rather than being filtered each
        # time it is read.
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO messages(session, role, content, ts, tokens) VALUES (?,?,?,?,?)",
                (session, message.role, redact(message.content), message.ts, message.tokens),
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
