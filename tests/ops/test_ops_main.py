"""CLI tests: python -m msb_v3.ops backup|restore.

ops/__main__.py was a 0%-coverage gap — the operational entrypoint of the
safety/ops layer had no tests at all. These pin the two subcommands
against hermetic tmp dirs: backup (create + prune) and restore (latest /
by name / no-backups failure).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import msb_v3.ops.__main__ as ops_main
from msb_v3.ops.backup import create_backup


@pytest.fixture()
def iso_paths(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    """Bind the CLI's default_paths() to tmp and reset sys.argv so main()
    parses exactly what each test feeds it."""
    data = tmp_path / "data"
    storage = tmp_path / "storage"
    dest = tmp_path / "backups"
    data.mkdir()
    storage.mkdir()
    (data / "notes.txt").write_text("hello")
    (storage / "vectors.bin").write_bytes(b"\x00\x01")
    monkeypatch.setattr(ops_main, "default_paths", lambda: (data, storage, dest))
    monkeypatch.setattr(sys, "argv", ["msb_v3.ops"])
    return data, storage, dest


def test_backup_creates_backup_with_manifest(iso_paths, capsys) -> None:
    _data, _storage, dest = iso_paths
    sys.argv = ["msb_v3.ops", "backup", "--keep", "3"]
    ops_main.main()
    out = capsys.readouterr().out
    assert "[backup]" in out
    assert "dbs=0" in out
    backups = sorted(dest.iterdir())
    assert len(backups) == 1
    assert (backups[0] / "manifest.json").is_file()


def test_backup_prunes_over_keep(iso_paths, capsys) -> None:
    data, storage, dest = iso_paths
    create_backup(data, storage, dest, timestamp="20260801T000000Z")
    create_backup(data, storage, dest, timestamp="20260802T000000Z")
    sys.argv = ["msb_v3.ops", "backup", "--keep", "1"]
    ops_main.main()
    out = capsys.readouterr().out
    # main() creates a third backup (fresh timestamp) then prunes down to
    # --keep 1, so exactly two older backups get removed.
    assert "pruned=2" in out
    assert len(list(dest.iterdir())) == 1


def test_restore_latest_roundtrip(iso_paths, capsys) -> None:
    data, storage, dest = iso_paths
    create_backup(data, storage, dest, timestamp="20260803T000000Z")
    # Corrupt the live data after backing up; restore must bring it back.
    (data / "notes.txt").write_text("corrupted")
    sys.argv = ["msb_v3.ops", "restore", "latest"]
    ops_main.main()
    out = capsys.readouterr().out
    assert "[restore] restored from" in out
    assert (data / "notes.txt").read_text() == "hello"
    assert (storage / "vectors.bin").read_bytes() == b"\x00\x01"


def test_restore_by_name(iso_paths, capsys) -> None:
    data, storage, dest = iso_paths
    create_backup(data, storage, dest, timestamp="20260804T000000Z")
    sys.argv = ["msb_v3.ops", "restore", "20260804T000000Z"]
    ops_main.main()
    assert "[restore] restored from" in capsys.readouterr().out


def test_restore_with_no_backups_fails(iso_paths) -> None:
    _data, _storage, dest = iso_paths
    assert not dest.exists() or not list(dest.iterdir())
    sys.argv = ["msb_v3.ops", "restore", "latest"]
    with pytest.raises(SystemExit, match="no backups found"):
        ops_main.main()
