"""BudgetLedger — persistent, fail-closed per-category budget caps.

The flywheel's generative (Yang) engine spends against hard caps on
research calls, tokens, and loop iterations per rolling window. When a cap
is hit the loop must halt — fail-closed, never degrade to "keep going".

Cap semantics: limit < 0 = unlimited (explicit opt-in), limit == 0 = deny
everything, limit > 0 = cap. An unknown category defaults to unlimited,
which is deliberate: new spend categories must be *configured* before they
can be capped, and denying unknown categories would break every caller
until the config catches up.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Dict, Optional

from msb_v3.core.config import settings
from msb_v3.governance.db import default_db_path

# The spend categories the blueprint names for hard caps.
CATEGORIES = ("research_calls", "tokens", "iterations")


class BudgetLedger:
    """Rolling-window counters persisted in SQLite (survive restarts)."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        limits: Optional[Dict[str, int]] = None,
        window_s: int = 86400,
    ) -> None:
        self.db_path = str(default_db_path() if db_path is None else db_path)
        self._limits = dict(limits or {})
        self._window_s = window_s
        self._lock = threading.Lock()
        self._init_db()

    @classmethod
    def from_settings(cls) -> "BudgetLedger":
        s = settings
        return cls(
            limits={
                "research_calls": s.gov_budget_research_calls,
                "tokens": s.gov_budget_tokens,
                "iterations": s.gov_budget_iterations,
            },
            window_s=s.gov_budget_window_min * 60,
        )

    def _init_db(self) -> None:
        from pathlib import Path

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS budget_entries ("
                " category TEXT NOT NULL,"
                " period_start REAL NOT NULL,"
                " spent INTEGER NOT NULL DEFAULT 0,"
                " PRIMARY KEY (category, period_start))"
            )

    def _row(self, conn: sqlite3.Connection, category: str):
        now = time.time()
        row = conn.execute(
            "SELECT period_start, spent FROM budget_entries WHERE category=?",
            (category,),
        ).fetchone()
        if row is None or now - row[0] >= self._window_s:
            return None
        return row

    def spend(self, category: str, amount: int = 1) -> bool:
        """Consume ``amount`` toward the category cap. True = allowed."""
        limit = self._limits.get(category, -1)
        if limit == 0:
            return False  # fail-closed: a zero cap denies everything
        if limit < 0:
            return True  # explicit unlimited
        if amount > limit:
            return False
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                row = self._row(conn, category)
                now = time.time()
                if row is None:
                    conn.execute("DELETE FROM budget_entries WHERE category=?", (category,))
                    conn.execute(
                        "INSERT INTO budget_entries(category, period_start, spent) VALUES (?,?,?)",
                        (category, now, amount),
                    )
                    return True
                spent = row[1] + amount
                if spent > limit:
                    return False  # cap hit — record nothing, caller must halt
                conn.execute(
                    "UPDATE budget_entries SET spent=? WHERE category=?",
                    (spent, category),
                )
                return True

    def reset(self, category: Optional[str] = None) -> None:
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                if category is None:
                    conn.execute("DELETE FROM budget_entries")
                else:
                    conn.execute("DELETE FROM budget_entries WHERE category=?", (category,))

    def state(self) -> Dict[str, dict]:
        """Per-category snapshot: spent, limit, remaining, window, period."""
        out: Dict[str, dict] = {}
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                for cat in CATEGORIES:
                    row = self._row(conn, cat)
                    spent = row[1] if row else 0
                    limit = self._limits.get(cat, -1)
                    out[cat] = {
                        "spent": spent,
                        "limit": limit,
                        "remaining": -1 if limit < 0 else max(limit - spent, 0),
                        "window_s": self._window_s,
                        "period_start": row[0] if row else time.time(),
                    }
        return out
