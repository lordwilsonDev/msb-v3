"""Tests for the lightweight SQLite schema versioning system."""

from __future__ import annotations

import tempfile
from pathlib import Path

from msb_v3.db.migrations import (
    Migration,
    ensure_schema,
    get_schema_version,
    list_versions,
)


def _tmp_db(name: str = "test.db") -> Path:
    return Path(tempfile.mkdtemp()) / name


# --- Version tracking ---


def test_fresh_db_starts_at_version_zero():
    db = _tmp_db()
    assert get_schema_version(db) == 0


def test_nonexistent_db_returns_zero():
    assert get_schema_version("/nonexistent/path/test.db") == 0


# --- Migration application ---


def test_single_migration():
    db = _tmp_db()
    migrations = [
        Migration(1, "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)"),
    ]
    final = ensure_schema(db, "test", migrations)
    assert final == 1

    # Verify the table was created
    import sqlite3

    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    tables = {r[0] for r in row}
    assert "items" in tables
    assert "_schema_version" in tables
    conn.close()


def test_multiple_migrations_applied_in_order():
    db = _tmp_db()
    migrations = [
        Migration(1, "CREATE TABLE items (id INTEGER PRIMARY KEY)"),
        Migration(2, "ALTER TABLE items ADD COLUMN name TEXT DEFAULT ''"),
        Migration(3, "ALTER TABLE items ADD COLUMN score INTEGER DEFAULT 0"),
    ]
    final = ensure_schema(db, "test", migrations)
    assert final == 3

    import sqlite3

    conn = sqlite3.connect(str(db))
    # Check that all columns exist via PRAGMA
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
    assert "id" in cols
    assert "name" in cols
    assert "score" in cols
    conn.close()


def test_idempotent_on_already_applied():
    db = _tmp_db()
    migrations = [
        Migration(1, "CREATE TABLE items (id INTEGER PRIMARY KEY)"),
    ]
    ensure_schema(db, "test", migrations)
    # Apply again — should be a no-op
    final = ensure_schema(db, "test", migrations)
    assert final == 1


def test_partial_migration():
    """Apply v1 and v3, skip v2 — v3 should still apply if it doesn't depend on v2."""
    db = _tmp_db()
    migrations_v1 = [Migration(1, "CREATE TABLE items (id INTEGER PRIMARY KEY)")]
    ensure_schema(db, "test", migrations_v1)

    # Now add v3 (independent of v2)
    migrations_all = [
        Migration(1, "CREATE TABLE items (id INTEGER PRIMARY KEY)"),
        Migration(2, "ALTER TABLE items ADD COLUMN name TEXT"),
        Migration(3, "CREATE TABLE logs (id INTEGER PRIMARY KEY)"),
    ]
    final = ensure_schema(db, "test", migrations_all)
    assert final == 3

    import sqlite3

    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "items" in tables
    assert "logs" in tables
    conn.close()


def test_migration_failure_rolls_back():
    db = _tmp_db()
    migrations = [
        Migration(1, "CREATE TABLE items (id INTEGER PRIMARY KEY)"),
        Migration(2, "INVALID SQL THIS WILL FAIL"),  # noqa: S105
    ]

    try:
        ensure_schema(db, "test", migrations)
    except Exception:
        pass

    # Version should still be 1 (the good migration)
    assert get_schema_version(db) == 1


def test_migration_failure_does_not_advance_version():
    db = _tmp_db()
    migrations = [
        Migration(1, "CREATE TABLE items (id INTEGER PRIMARY KEY)"),
        Migration(2, "ALTER TABLE items ADD COLUMN name TEXT"),
        Migration(3, "THIS IS INVALID SQL"),  # noqa: S105
    ]

    try:
        ensure_schema(db, "test", migrations)
    except Exception:
        pass

    # Should be at v2 (v1 and v2 succeeded, v3 failed)
    assert get_schema_version(db) == 2


# --- Version listing ---


def test_list_versions_finds_databases():
    tmpdir = Path(tempfile.mkdtemp())
    db1 = tmpdir / "sub1.db"
    db2 = tmpdir / "sub2.db"

    ensure_schema(
        db1,
        "sub1",
        [Migration(1, "CREATE TABLE t1 (id INTEGER PRIMARY KEY)")],
    )
    ensure_schema(
        db2,
        "sub2",
        [
            Migration(1, "CREATE TABLE t2 (id INTEGER PRIMARY KEY)"),
            Migration(2, "ALTER TABLE t2 ADD COLUMN x TEXT"),
        ],
    )

    versions = list_versions(tmpdir)
    assert versions["sub1.db"] == 1
    assert versions["sub2.db"] == 2


def test_list_versions_empty_dir():
    tmpdir = Path(tempfile.mkdtemp())
    versions = list_versions(tmpdir)
    assert versions == {}


def test_list_versions_nonexistent_dir():
    versions = list_versions("/nonexistent/path")
    assert versions == {}


# --- Edge cases ---


def test_empty_migrations_list():
    db = _tmp_db()
    final = ensure_schema(db, "test", [])
    assert final == 0


def test_migration_with_multiple_statements():
    db = _tmp_db()
    migrations = [
        Migration(
            1,
            "CREATE TABLE items (id INTEGER PRIMARY KEY); "
            "CREATE TABLE logs (id INTEGER PRIMARY KEY, item_id INTEGER)",
        ),
    ]
    final = ensure_schema(db, "test", migrations)
    assert final == 1

    import sqlite3

    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "items" in tables
    assert "logs" in tables
    conn.close()
