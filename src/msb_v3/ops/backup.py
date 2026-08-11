"""Tested backup/restore for msb-v3's only-copy data.

SQLite is copied via the online backup API so a snapshot is consistent even
while the server holds the db open. Qdrant storage/ and JSON state are file
copies. Everything lands in a timestamped folder OUTSIDE the repo.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path


def _backup_sqlite(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(src)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


@dataclass
class BackupManifest:
    path: Path
    timestamp: str
    checksums: dict[str, str]
    db_count: int


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_tree(src: Path, dst: Path, *, skip_db: bool) -> None:
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        if skip_db and item.suffix == ".db":
            continue
        rel = item.relative_to(src)
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, out)


def create_backup(data_dir: Path, storage_dir: Path, dest_root: Path, *, timestamp: str) -> BackupManifest:
    out = dest_root / timestamp
    out.mkdir(parents=True, exist_ok=True)

    db_count = 0
    for db in sorted(data_dir.rglob("*.db")):
        rel = db.relative_to(data_dir)
        _backup_sqlite(db, out / "data" / rel)
        db_count += 1

    _copy_tree(data_dir, out / "data", skip_db=True)
    if storage_dir.exists():
        _copy_tree(storage_dir, out / "storage", skip_db=False)

    checksums: dict[str, str] = {}
    for f in sorted(out.rglob("*")):
        if f.is_file() and f != out / "manifest.json":
            checksums[f.relative_to(out).as_posix()] = _sha256(f)

    manifest = BackupManifest(path=out, timestamp=timestamp, checksums=checksums, db_count=db_count)
    (out / "manifest.json").write_text(
        json.dumps({"timestamp": timestamp, "db_count": db_count, "checksums": checksums}, indent=2)
    )
    return manifest
