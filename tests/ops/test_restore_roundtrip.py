import shutil
import sqlite3
from pathlib import Path

import pytest

from msb_v3.ops.backup import create_backup, restore_backup, verify_backup


def _make_db(p: Path, val: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE t (k TEXT)")
    c.execute("INSERT INTO t VALUES (?)", (val,))
    c.commit()
    c.close()


def test_backup_then_wipe_then_restore_recovers_everything(tmp_path: Path):
    data = tmp_path / "data"
    storage = tmp_path / "storage"
    _make_db(data / "msb_v3.db", "primary")
    storage.mkdir()
    (storage / "seg.bin").write_bytes(b"index")

    m = create_backup(data, storage, tmp_path / "backups", timestamp="20260811T120000Z")
    assert verify_backup(m.path) is True

    # simulate disaster
    shutil.rmtree(data)
    shutil.rmtree(storage)

    restore_backup(m.path, data, storage)

    rows = sqlite3.connect(data / "msb_v3.db").execute("SELECT k FROM t").fetchall()
    assert rows == [("primary",)]
    assert (storage / "seg.bin").read_bytes() == b"index"


def test_restore_recovers_notary_log(tmp_path: Path):
    data = tmp_path / "data"
    storage = tmp_path / "storage"
    _make_db(data / "msb_v3.db", "primary")
    storage.mkdir()
    notary = tmp_path / "chain-anchor-notary.jsonl"
    notary.write_text('{"notarized": "tip"}\n')

    m = create_backup(
        data, storage, tmp_path / "backups", timestamp="20260811T120000Z", notary_log=notary
    )
    # disaster: notary log destroyed along with everything else
    notary.unlink()
    shutil.rmtree(data)
    shutil.rmtree(storage)

    restore_backup(m.path, data, storage, notary_dest=notary)

    assert notary.read_text() == '{"notarized": "tip"}\n'
    rows = sqlite3.connect(data / "msb_v3.db").execute("SELECT k FROM t").fetchall()
    assert rows == [("primary",)]


def test_restore_without_notary_dest_skips_notary(tmp_path: Path):
    data = tmp_path / "data"
    storage = tmp_path / "storage"
    _make_db(data / "msb_v3.db", "primary")
    storage.mkdir()
    notary = tmp_path / "chain-anchor-notary.jsonl"
    notary.write_text('{"notarized": "tip"}\n')
    m = create_backup(
        data, storage, tmp_path / "backups", timestamp="20260811T120000Z", notary_log=notary
    )
    notary.unlink()

    restore_backup(m.path, data, storage)  # no notary_dest -> untouched

    assert not notary.exists()


def test_restore_refuses_corrupted_backup(tmp_path: Path):
    data = tmp_path / "data"
    storage = tmp_path / "storage"
    _make_db(data / "msb_v3.db", "primary")
    storage.mkdir()
    m = create_backup(data, storage, tmp_path / "backups", timestamp="20260811T120000Z")
    (m.path / "data" / "msb_v3.db").write_bytes(b"tampered")
    assert verify_backup(m.path) is False
    with pytest.raises(ValueError):
        restore_backup(m.path, data, storage)


def test_failed_restore_leaves_existing_data_intact(tmp_path: Path):
    data = tmp_path / "data"
    storage = tmp_path / "storage"
    _make_db(data / "msb_v3.db", "primary")
    storage.mkdir()
    m = create_backup(data, storage, tmp_path / "backups", timestamp="20260811T120000Z")
    (m.path / "data" / "msb_v3.db").write_bytes(b"tampered")
    with pytest.raises(ValueError):
        restore_backup(m.path, data, storage)
    # original data must be untouched
    rows = sqlite3.connect(data / "msb_v3.db").execute("SELECT k FROM t").fetchall()
    assert rows == [("primary",)]


def test_verify_backup_false_on_malformed_manifest(tmp_path: Path):
    b = tmp_path / "b"
    b.mkdir()
    # missing manifest
    assert verify_backup(b) is False
    for bad in ("not json {{{", "[1, 2, 3]", '{"checksums": "nope"}'):
        (b / "manifest.json").write_text(bad)
        assert verify_backup(b) is False


def test_backup_excludes_wal_sidecars_and_restores_live_db(tmp_path: Path):
    data = tmp_path / "data"
    storage = tmp_path / "storage"
    storage.mkdir()
    db = data / "msb_v3.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (k TEXT)")
    conn.execute("INSERT INTO t VALUES ('committed')")
    conn.commit()
    # leave conn OPEN so -wal/-shm sidecars are present on disk during backup
    m = create_backup(data, storage, tmp_path / "backups", timestamp="20260811T120000Z")
    conn.close()
    assert not (m.path / "data" / "msb_v3.db-wal").exists()
    assert not (m.path / "data" / "msb_v3.db-shm").exists()
    shutil.rmtree(data)
    shutil.rmtree(storage)
    restore_backup(m.path, data, storage)
    rows = sqlite3.connect(data / "msb_v3.db").execute("SELECT k FROM t").fetchall()
    assert rows == [("committed",)]


def test_restore_midcopy_failure_leaves_original_intact(tmp_path: Path, monkeypatch):
    import msb_v3.ops.backup as backup_mod
    data = tmp_path / "data"
    storage = tmp_path / "storage"
    _make_db(data / "msb_v3.db", "primary")
    storage.mkdir()
    (storage / "seg.bin").write_bytes(b"orig")
    m = create_backup(data, storage, tmp_path / "backups", timestamp="20260811T120000Z")
    # valid backup, but copytree blows up mid-restore
    def boom(src, dst, *a, **k):
        raise OSError("simulated disk failure mid-copy")
    monkeypatch.setattr(backup_mod.shutil, "copytree", boom)
    with pytest.raises(OSError):
        restore_backup(m.path, data, storage)
    # original data + storage must be untouched (copytree never reached the real target)
    rows = sqlite3.connect(data / "msb_v3.db").execute("SELECT k FROM t").fetchall()
    assert rows == [("primary",)]
    assert (storage / "seg.bin").read_bytes() == b"orig"
