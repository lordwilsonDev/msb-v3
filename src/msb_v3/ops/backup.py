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


def verify_backup(backup_dir: Path) -> bool:
    try:
        manifest = json.loads((backup_dir / "manifest.json").read_text())
        checksums = manifest["checksums"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return False
    for rel, expected in checksums.items():
        f = backup_dir / rel
        if not f.is_file() or _sha256(f) != expected:
            return False
    return True


def restore_backup(backup_dir: Path, data_dir: Path, storage_dir: Path) -> None:
    if not verify_backup(backup_dir):
        raise ValueError(f"backup failed checksum verification: {backup_dir}")
    for name, target in (("data", data_dir), ("storage", storage_dir)):
        src = backup_dir / name
        if not src.exists():
            continue
        tmp = target.parent / (target.name + ".restore-tmp")
        old = target.parent / (target.name + ".restore-old")
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(src, tmp)          # build new copy first (non-destructive)
        if target.exists():
            if old.exists():
                shutil.rmtree(old)
            target.rename(old)             # move existing aside (atomic rename)
        tmp.rename(target)                 # swap in new copy (atomic rename)
        if old.exists():
            shutil.rmtree(old)             # drop old only after success
