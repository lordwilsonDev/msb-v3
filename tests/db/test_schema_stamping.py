"""H9 — every SQLite DB under data/ is schema-versioned.

The migration framework (test_migrations.py) shipped but was never wired to
the live databases. `stamp_all_db` walks the data directory and applies the
BASELINE migration so every DB has a version floor. These tests prove it is
safe (does not touch existing tables), idempotent, and covers new DBs — and
the live guard fails if any data/**/*.db is left unstamped.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from msb_v3.db.migrations import (
    BASELINE,
    Migration,
    ensure_schema,
    get_schema_version,
    stamp_all_db,
)

_REPO = Path(__file__).resolve().parents[2]


def _mkdb(path: Path, *, rows: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE payload (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany(
        "INSERT INTO payload (v) VALUES (?)", [(f"r{i}",) for i in range(rows)]
    )
    conn.commit()
    conn.close()


def test_stamp_all_db_stamps_every_db_below_v1():
    d = Path(tempfile.mkdtemp())
    _mkdb(d / "a.db")
    _mkdb(d / "sub" / "b.db")
    _mkdb(d / "sub" / "deep" / "c.db")

    result = stamp_all_db(d)

    assert set(result) == {"a.db", "sub/b.db", "sub/deep/c.db"}
    assert all(v == 1 for v in result.values()), result
    for rel in result:
        assert get_schema_version(d / rel) == 1


def test_baseline_does_not_touch_existing_data():
    d = Path(tempfile.mkdtemp())
    db = d / "populated.db"
    _mkdb(db, rows=500)

    stamp_all_db(d)

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM payload").fetchone()[0] == 500
        # the version + marker tables exist, and nothing else was added
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "payload" in tables
        assert "_schema_version" in tables
        assert "_schema_baseline" in tables
        assert tables == {"payload", "_schema_version", "_schema_baseline"}
    finally:
        conn.close()


def test_stamp_all_db_is_idempotent():
    d = Path(tempfile.mkdtemp())
    _mkdb(d / "a.db", rows=10)

    first = stamp_all_db(d)
    second = stamp_all_db(d)

    assert first == second == {"a.db": 1}
    conn = sqlite3.connect(d / "a.db")
    try:
        # version did not advance and the marker table is intact
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE name='_schema_baseline'"
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


def test_stamp_all_db_covers_a_newly_created_db():
    d = Path(tempfile.mkdtemp())
    _mkdb(d / "old.db")
    stamp_all_db(d)
    _mkdb(d / "new.db")  # a store adds a DB after the first sweep

    result = stamp_all_db(d)

    assert result["new.db"] == 1
    assert get_schema_version(d / "new.db") == 1


def test_higher_version_is_not_downgraded():
    d = Path(tempfile.mkdtemp())
    db = d / "ahead.db"
    _mkdb(db)
    ensure_schema(
        db,
        "ahead",
        [BASELINE, Migration(2, "CREATE TABLE IF NOT EXISTS extra (x INTEGER)")],
    )
    assert get_schema_version(db) == 2

    result = stamp_all_db(d)

    assert result["ahead.db"] == 2  # unchanged


def test_missing_data_dir_returns_empty():
    assert stamp_all_db(Path(tempfile.mkdtemp()) / "does-not-exist") == {}


def test_live_data_dir_is_fully_stamped():
    """Regression lock: every SQLite DB under the repo's data/ must be at
    schema v1+. A new store that creates an unstamped DB fails here — run
    `python scripts/stamp-schemas.py`.

    Only meaningful against the real working tree. The portability gate stages
    a `data/` copy into /tmp and runs the suite there; a WAL sidecar that
    hasn't checkpointed makes copied DBs read as v0 — not a real regression.
    Skip when running from a staged / foreign checkout.
    """
    import os

    import pytest

    data_dir = _REPO / "data"
    if not data_dir.is_dir():
        pytest.skip("no data/ directory in this checkout")
    if not (_REPO / ".git").exists():
        pytest.skip("staged / foreign checkout (no .git) — not the live data dir")
    if str(_REPO).startswith("/tmp") or os.environ.get("MSB_HOME", "").startswith("/tmp"):
        pytest.skip("staged copy under /tmp — copied DBs may lag the WAL")

    unstamped = {
        str(p.relative_to(data_dir)): get_schema_version(p)
        for p in sorted(data_dir.rglob("*.db"))
        if get_schema_version(p) < 1
    }
    assert not unstamped, (
        f"unstamped SQLite DBs under data/: {unstamped}. "
        f"Run `python scripts/stamp-schemas.py`."
    )
