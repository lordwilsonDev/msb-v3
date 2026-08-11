import json
import sqlite3
from pathlib import Path
from msb_v3.ops.backup import create_backup


def _make_db(p: Path, val: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE t (k TEXT)")
    c.execute("INSERT INTO t VALUES (?)", (val,))
    c.commit()
    c.close()


def test_create_backup_captures_db_files_and_storage(tmp_path: Path):
    data = tmp_path / "data"
    storage = tmp_path / "storage"
    _make_db(data / "msb_v3.db", "primary")
    _make_db(data / "uac" / "audit_chain.db", "audit")
    (data / "triumvirate").mkdir(parents=True)
    (data / "triumvirate" / "plan_state.json").write_text('{"k":1}')
    storage.mkdir()
    (storage / "seg.bin").write_bytes(b"index-bytes")

    m = create_backup(data, storage, tmp_path / "backups", timestamp="20260811T120000Z")

    assert m.db_count == 2
    assert (m.path / "data" / "msb_v3.db").exists()
    assert (m.path / "data" / "triumvirate" / "plan_state.json").exists()
    assert (m.path / "storage" / "seg.bin").read_bytes() == b"index-bytes"
    manifest = json.loads((m.path / "manifest.json").read_text())
    assert manifest["db_count"] == 2
    assert "data/msb_v3.db" in manifest["checksums"]
