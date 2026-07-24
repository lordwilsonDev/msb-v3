"""Database module — single SQLite connection pool."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from msb_v3.core.config import settings


def _ensure_db_path() -> Path:
    p = Path(settings.db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_ensure_db_path()), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction() -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
