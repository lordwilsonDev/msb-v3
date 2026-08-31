"""Lightweight SQLite schema versioning.

Every database gets a ``_schema_version`` table that tracks the current
schema version.  On startup, ``ensure_schema()`` checks the version and
applies any pending migrations in order.

Usage::

    from msb_v3.db.migrations import Migration, ensure_schema

    MIGRATIONS = [
        Migration(1, "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY)"),
        Migration(2, "ALTER TABLE items ADD COLUMN name TEXT DEFAULT ''"),
    ]

    ensure_schema(db_path, "my_subsystem", MIGRATIONS)

Each migration is an ``(version, sql)`` pair.  Migrations are applied
atomically — if one fails, the transaction rolls back and the version
is not advanced.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

_VERSION_TABLE = "_schema_version"


@dataclass(frozen=True, slots=True)
class Migration:
    """A single schema migration."""

    version: int
    sql: str


def _get_version(conn: sqlite3.Connection) -> int:
    """Read the current schema version, or 0 if not yet initialized."""
    try:
        row = conn.execute(
            f"SELECT version FROM {_VERSION_TABLE} WHERE id = 1"
        ).fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    """Set the schema version."""
    conn.execute(f"DELETE FROM {_VERSION_TABLE}")
    conn.execute(
        f"INSERT INTO {_VERSION_TABLE} (id, version) VALUES (1, ?)",
        (version,),
    )


def _init_version_table(conn: sqlite3.Connection) -> None:
    """Create the version tracking table if it doesn't exist."""
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {_VERSION_TABLE} (
            id INTEGER PRIMARY KEY,
            version INTEGER NOT NULL
        )"""
    )


def ensure_schema(
    db_path: Path | str,
    subsystem: str,
    migrations: Sequence[Migration],
) -> int:
    """Apply pending migrations to a SQLite database.

    Returns the final schema version.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _init_version_table(conn)
        current = _get_version(conn)

        pending = [m for m in migrations if m.version > current]
        if not pending:
            logger.debug(
                "%s schema up to date (version %d)", subsystem, current
            )
            return current

        # Sort by version to ensure order
        pending.sort(key=lambda m: m.version)

        for migration in pending:
            logger.info(
                "%s: applying migration v%d", subsystem, migration.version
            )
            try:
                conn.executescript("BEGIN; " + migration.sql + "; COMMIT;")
                _set_version(conn, migration.version)
                conn.commit()
            except Exception:
                conn.rollback()
                logger.error(
                    "%s: migration v%d FAILED — rolling back",
                    subsystem,
                    migration.version,
                )
                raise

        final = _get_version(conn)
        logger.info(
            "%s: schema updated %d → %d (%d migrations applied)",
            subsystem,
            current,
            final,
            len(pending),
        )
        # Fold the WAL back into the main .db file so the version is visible to
        # a plain file copy (portability gate, ops/backup). Best-effort: a
        # live reader on the DB caps it at PASSIVE, which is enough for the
        # small _schema_version write.
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            pass
        return final
    finally:
        conn.close()


def get_schema_version(db_path: Path | str) -> int:
    """Read the current schema version without applying migrations."""
    db_path = Path(db_path)
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        _init_version_table(conn)
        return _get_version(conn)
    finally:
        conn.close()


def list_versions(db_dir: Path | str) -> dict[str, int]:
    """Scan a directory for .db files and report their schema versions."""
    db_dir = Path(db_dir)
    versions: dict[str, int] = {}
    if not db_dir.exists():
        return versions
    for db_file in sorted(db_dir.rglob("*.db")):
        rel = str(db_file.relative_to(db_dir))
        versions[rel] = get_schema_version(db_file)
    return versions


# The baseline migration every tracked DB gets: it does not touch any existing
# table — it only records that schema versioning was adopted, so v2+ migrations
# have a floor to build on. Idempotent (IF NOT EXISTS) and safe on a populated
# database.
BASELINE = Migration(
    1,
    "CREATE TABLE IF NOT EXISTS _schema_baseline "
    "(adopted_at TEXT NOT NULL DEFAULT (datetime('now')))",
)

# Per-DB migration lists. A DB not listed here still gets BASELINE via
# stamp_all_db's directory walk; add an entry only when a subsystem needs a
# real v2+ migration.
REGISTRY: dict[str, Sequence[Migration]] = {}


def stamp_all_db(db_dir: Path | str) -> dict[str, int]:
    """Ensure every ``*.db`` under ``db_dir`` is schema-versioned.

    Walks the directory (so new databases are covered automatically), applies
    ``BASELINE`` to anything below v1, plus any subsystem-specific migrations
    from ``REGISTRY`` (keyed by path relative to ``db_dir``). Best-effort
    per-DB: a locked or corrupt file is logged and skipped, never fatal.
    Returns ``{relative_path: final_version}``.
    """
    db_dir = Path(db_dir)
    out: dict[str, int] = {}
    if not db_dir.exists():
        return out
    for db_file in sorted(db_dir.rglob("*.db")):
        rel = str(db_file.relative_to(db_dir))
        migrations = list(dict.fromkeys((BASELINE, *REGISTRY.get(rel, ()))))
        migrations.sort(key=lambda m: m.version)
        try:
            out[rel] = ensure_schema(db_file, rel, migrations)
        except Exception as exc:  # noqa: BLE001 — one bad DB must not stop the sweep
            logger.error("stamp_all_db: %s failed: %s", rel, exc)
            out[rel] = -1
    return out
