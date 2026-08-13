import sqlite3
from pathlib import Path

from msb_v3.ops.backup import _backup_sqlite


def test_backup_sqlite_copies_rows(tmp_path: Path):
    src = tmp_path / "src.db"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE t (k TEXT)")
    conn.execute("INSERT INTO t VALUES ('alive')")
    conn.commit()
    conn.close()

    dst = tmp_path / "out" / "src.db"
    _backup_sqlite(src, dst)

    rows = sqlite3.connect(dst).execute("SELECT k FROM t").fetchall()
    assert rows == [("alive",)]
