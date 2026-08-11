import sqlite3
import shutil
import pytest
from pathlib import Path
from msb_v3.ops.backup import create_backup, restore_backup, verify_backup

def _make_db(p: Path, val: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p); c.execute("CREATE TABLE t (k TEXT)")
    c.execute("INSERT INTO t VALUES (?)", (val,)); c.commit(); c.close()

def test_backup_then_wipe_then_restore_recovers_everything(tmp_path: Path):
    data = tmp_path / "data"; storage = tmp_path / "storage"
    _make_db(data / "msb_v3.db", "primary")
    storage.mkdir(); (storage / "seg.bin").write_bytes(b"index")

    m = create_backup(data, storage, tmp_path / "backups", timestamp="20260811T120000Z")
    assert verify_backup(m.path) is True

    # simulate disaster
    shutil.rmtree(data); shutil.rmtree(storage)

    restore_backup(m.path, data, storage)

    rows = sqlite3.connect(data / "msb_v3.db").execute("SELECT k FROM t").fetchall()
    assert rows == [("primary",)]
    assert (storage / "seg.bin").read_bytes() == b"index"

def test_restore_refuses_corrupted_backup(tmp_path: Path):
    data = tmp_path / "data"; storage = tmp_path / "storage"
    _make_db(data / "msb_v3.db", "primary"); storage.mkdir()
    m = create_backup(data, storage, tmp_path / "backups", timestamp="20260811T120000Z")
    (m.path / "data" / "msb_v3.db").write_bytes(b"tampered")
    assert verify_backup(m.path) is False
    with pytest.raises(ValueError):
        restore_backup(m.path, data, storage)
