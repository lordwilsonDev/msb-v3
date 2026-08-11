# Phase 0A — Protect the Work: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give MSB v3 a tested, automated backup-and-restore for its only-copy data, so a disk failure or corruption can never cost the work.

**Architecture:** A small `msb_v3.ops.backup` module does consistent SQLite snapshots (online backup API, safe while the server runs) plus a file copy of Qdrant `storage/`, writing a checksummed manifest into a timestamped folder **outside the repo** (`~/msb-backups/msb-v3/<ts>/`). A restore reverses it. A pytest round-trip test proves restore actually works. A launchd agent runs it daily with retention.

**Tech Stack:** Python ≥3.11 (`sqlite3`, `hashlib`, `shutil`, `pathlib`, `dataclasses`), pytest, launchd.

## Global Constraints

- Python: `>=3.11`; interpreter `/opt/homebrew/Caskroom/miniforge/base/bin/python`.
- Tests live under `tests/`; run with `python -m pytest`. `ruff check` must stay clean.
- Package layout is `src/` (`src/msb_v3/...`).
- Backups are written **outside the repo**, default root `~/msb-backups/msb-v3/`, override `MSB_BACKUP_DIR`. Never write backups inside the working tree.
- Data to protect (real paths, verified 2026-08-11): everything under `data/` (six `.db` files + JSON state) and Qdrant `storage/`.
- Commit message style: `feat(ops): ...`, end with the repo's Co-Authored-By trailer.

---

### Task 1: `ops` package + consistent SQLite snapshot

**Files:**
- Create: `src/msb_v3/ops/__init__.py`
- Create: `src/msb_v3/ops/backup.py`
- Test: `tests/ops/test_backup_sqlite.py`

**Interfaces:**
- Produces: `_backup_sqlite(src: Path, dst: Path) -> None` — consistent copy of a live SQLite db via the online backup API. Later tasks call this per `.db`.

- [ ] **Step 1: Write the failing test**

```python
# tests/ops/test_backup_sqlite.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ops/test_backup_sqlite.py -v`
Expected: FAIL — `ModuleNotFoundError: msb_v3.ops.backup`

- [ ] **Step 3: Write minimal implementation**

```python
# src/msb_v3/ops/__init__.py
"""Operational tooling for msb-v3 (backup/restore, etc.)."""
```

```python
# src/msb_v3/ops/backup.py
"""Tested backup/restore for msb-v3's only-copy data.

SQLite is copied via the online backup API so a snapshot is consistent even
while the server holds the db open. Qdrant storage/ and JSON state are file
copies. Everything lands in a timestamped folder OUTSIDE the repo.
"""
from __future__ import annotations

import sqlite3
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ops/test_backup_sqlite.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/msb_v3/ops/__init__.py src/msb_v3/ops/backup.py tests/ops/test_backup_sqlite.py
git commit -m "feat(ops): consistent SQLite snapshot helper"
```

---

### Task 2: Full backup with checksummed manifest

**Files:**
- Modify: `src/msb_v3/ops/backup.py`
- Test: `tests/ops/test_backup_create.py`

**Interfaces:**
- Consumes: `_backup_sqlite` (Task 1).
- Produces:
  - `@dataclass BackupManifest` with fields `path: Path`, `timestamp: str`, `checksums: dict[str, str]` (backup-relative posix path → sha256 hex), `db_count: int`.
  - `create_backup(data_dir: Path, storage_dir: Path, dest_root: Path, *, timestamp: str) -> BackupManifest` — snapshots every `*.db` under `data_dir` via `_backup_sqlite`, copies all other files under `data_dir` and all of `storage_dir` verbatim, writes `manifest.json`, returns the manifest.

- [ ] **Step 1: Write the failing test**

```python
# tests/ops/test_backup_create.py
import json, sqlite3
from pathlib import Path
from msb_v3.ops.backup import create_backup

def _make_db(p: Path, val: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p); c.execute("CREATE TABLE t (k TEXT)")
    c.execute("INSERT INTO t VALUES (?)", (val,)); c.commit(); c.close()

def test_create_backup_captures_db_files_and_storage(tmp_path: Path):
    data = tmp_path / "data"; storage = tmp_path / "storage"
    _make_db(data / "msb_v3.db", "primary")
    _make_db(data / "uac" / "audit_chain.db", "audit")
    (data / "triumvirate").mkdir(parents=True)
    (data / "triumvirate" / "plan_state.json").write_text('{"k":1}')
    storage.mkdir(); (storage / "seg.bin").write_bytes(b"index-bytes")

    m = create_backup(data, storage, tmp_path / "backups", timestamp="20260811T120000Z")

    assert m.db_count == 2
    assert (m.path / "data" / "msb_v3.db").exists()
    assert (m.path / "data" / "triumvirate" / "plan_state.json").exists()
    assert (m.path / "storage" / "seg.bin").read_bytes() == b"index-bytes"
    manifest = json.loads((m.path / "manifest.json").read_text())
    assert manifest["db_count"] == 2
    assert "data/msb_v3.db" in manifest["checksums"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ops/test_backup_create.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_backup'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/msb_v3/ops/backup.py`:

```python
import hashlib
import json
import shutil
from dataclasses import dataclass


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
        if f.is_file() and f.name != "manifest.json":
            checksums[f.relative_to(out).as_posix()] = _sha256(f)

    manifest = BackupManifest(path=out, timestamp=timestamp, checksums=checksums, db_count=db_count)
    (out / "manifest.json").write_text(
        json.dumps({"timestamp": timestamp, "db_count": db_count, "checksums": checksums}, indent=2)
    )
    return manifest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ops/test_backup_create.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/msb_v3/ops/backup.py tests/ops/test_backup_create.py
git commit -m "feat(ops): full backup with checksummed manifest"
```

---

### Task 3: Restore + full round-trip test (the crown jewel)

**Files:**
- Modify: `src/msb_v3/ops/backup.py`
- Test: `tests/ops/test_restore_roundtrip.py`

**Interfaces:**
- Consumes: `create_backup`, `BackupManifest` (Task 2).
- Produces:
  - `verify_backup(backup_dir: Path) -> bool` — recompute every checksum, compare to `manifest.json`; `True` only if all match.
  - `restore_backup(backup_dir: Path, data_dir: Path, storage_dir: Path) -> None` — verify first (raise `ValueError` if mismatch), then replace `data_dir` and `storage_dir` contents from the backup.

- [ ] **Step 1: Write the failing test**

```python
# tests/ops/test_restore_roundtrip.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ops/test_restore_roundtrip.py -v`
Expected: FAIL — `ImportError: cannot import name 'restore_backup'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/msb_v3/ops/backup.py`:

```python
def verify_backup(backup_dir: Path) -> bool:
    manifest = json.loads((backup_dir / "manifest.json").read_text())
    for rel, expected in manifest["checksums"].items():
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
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ops/test_restore_roundtrip.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add src/msb_v3/ops/backup.py tests/ops/test_restore_roundtrip.py
git commit -m "feat(ops): verified restore with round-trip + tamper-refusal tests"
```

---

### Task 4: CLI + Makefile targets + retention

**Files:**
- Modify: `src/msb_v3/ops/backup.py`
- Create: `src/msb_v3/ops/__main__.py`
- Modify: `Makefile`
- Test: `tests/ops/test_cli_and_retention.py`

**Interfaces:**
- Consumes: `create_backup`, `restore_backup`, `list_backups`.
- Produces:
  - `list_backups(dest_root: Path) -> list[Path]` — timestamp-sorted backup dirs, newest last.
  - `prune_backups(dest_root: Path, keep: int) -> list[Path]` — delete all but the newest `keep`; return deleted paths.
  - `default_paths() -> tuple[Path, Path, Path]` — `(data_dir, storage_dir, dest_root)` from `settings.msb_home` and `MSB_BACKUP_DIR` (default `~/msb-backups/msb-v3`).
  - CLI: `python -m msb_v3.ops backup [--keep N]` and `python -m msb_v3.ops restore <timestamp|latest>`.

- [ ] **Step 1: Write the failing test**

```python
# tests/ops/test_cli_and_retention.py
from pathlib import Path
from msb_v3.ops.backup import prune_backups, list_backups

def test_prune_keeps_newest_n(tmp_path: Path):
    for ts in ["20260101T000000Z", "20260102T000000Z", "20260103T000000Z"]:
        (tmp_path / ts).mkdir()
    deleted = prune_backups(tmp_path, keep=2)
    remaining = [p.name for p in list_backups(tmp_path)]
    assert remaining == ["20260102T000000Z", "20260103T000000Z"]
    assert [p.name for p in deleted] == ["20260101T000000Z"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/ops/test_cli_and_retention.py -v`
Expected: FAIL — `ImportError: cannot import name 'prune_backups'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/msb_v3/ops/backup.py`:

```python
import os
from msb_v3.core.config import settings


def list_backups(dest_root: Path) -> list[Path]:
    if not dest_root.exists():
        return []
    return sorted((p for p in dest_root.iterdir() if p.is_dir()), key=lambda p: p.name)


def prune_backups(dest_root: Path, keep: int) -> list[Path]:
    backups = list_backups(dest_root)
    doomed = backups[:-keep] if keep > 0 else []
    for p in doomed:
        shutil.rmtree(p)
    return doomed


def default_paths() -> tuple[Path, Path, Path]:
    home = Path(settings.msb_home)
    dest = Path(os.getenv("MSB_BACKUP_DIR", str(Path.home() / "msb-backups" / "msb-v3")))
    return home / "data", home / "storage", dest
```

Create `src/msb_v3/ops/__main__.py`:

```python
"""CLI: python -m msb_v3.ops backup [--keep N] | restore <timestamp|latest>"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from msb_v3.ops.backup import (
    create_backup, restore_backup, list_backups, prune_backups, default_paths,
)


def main() -> None:
    data_dir, storage_dir, dest_root = default_paths()
    ap = argparse.ArgumentParser(prog="msb_v3.ops")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup"); b.add_argument("--keep", type=int, default=14)
    r = sub.add_parser("restore"); r.add_argument("which")
    args = ap.parse_args()

    if args.cmd == "backup":
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        m = create_backup(data_dir, storage_dir, dest_root, timestamp=ts)
        pruned = prune_backups(dest_root, keep=args.keep)
        print(f"[backup] {m.path}  dbs={m.db_count}  pruned={len(pruned)}")
    elif args.cmd == "restore":
        backups = list_backups(dest_root)
        if not backups:
            raise SystemExit("no backups found")
        target = backups[-1] if args.which == "latest" else dest_root / args.which
        restore_backup(target, data_dir, storage_dir)
        print(f"[restore] restored from {target}")


if __name__ == "__main__":
    main()
```

Add to `Makefile`:

```makefile
backup:
	$(PY) -m msb_v3.ops backup

restore:
	$(PY) -m msb_v3.ops restore $(TS)

backup-verify:
	$(PY) -m pytest tests/ops -q
```

> Note: reuse the Makefile's existing `PY` variable (the miniforge interpreter). If none exists, add `PY := /opt/homebrew/Caskroom/miniforge/base/bin/python` near the top.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ops/test_cli_and_retention.py -v`
Expected: PASS

- [ ] **Step 5: Smoke the real CLI + commit**

```bash
/opt/homebrew/Caskroom/miniforge/base/bin/python -m msb_v3.ops backup --keep 14
git add src/msb_v3/ops/backup.py src/msb_v3/ops/__main__.py Makefile tests/ops/test_cli_and_retention.py
git commit -m "feat(ops): backup/restore CLI, retention, and make targets"
```

---

### Task 5: Daily scheduled backup (launchd)

**Files:**
- Create: `scripts/launchd/com.lordwilson.msb-backup.plist`
- Create: `scripts/backup.sh`
- Modify: `docs/blueprints/2026-08-11-adaptive-build-environment.md` (tick Phase 0 backup item)

**Interfaces:**
- Consumes: `python -m msb_v3.ops backup` (Task 4).

- [ ] **Step 1: Write the runner script**

```bash
# scripts/backup.sh
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec /opt/homebrew/Caskroom/miniforge/base/bin/python -m msb_v3.ops backup --keep 14
```

- [ ] **Step 2: Make it executable and run once**

Run:
```bash
chmod +x scripts/backup.sh && ./scripts/backup.sh
```
Expected: prints `[backup] /Users/lordwilson/msb-backups/msb-v3/<ts>  dbs=6 ...`

- [ ] **Step 3: Write the launchd plist (daily 03:00, matches existing agent style)**

```xml
<!-- scripts/launchd/com.lordwilson.msb-backup.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.lordwilson.msb-backup</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/lordwilson/msb-v3/scripts/backup.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/Users/lordwilson/msb-v3/logs/backup.log</string>
  <key>StandardErrorPath</key><string>/Users/lordwilson/msb-v3/logs/backup.err</string>
  <key>WorkingDirectory</key><string>/Users/lordwilson/msb-v3</string>
</dict>
</plist>
```

- [ ] **Step 4: Install and verify the agent**

Run:
```bash
cp scripts/launchd/com.lordwilson.msb-backup.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lordwilson.msb-backup.plist
launchctl print gui/$(id -u)/com.lordwilson.msb-backup | head -20
```
Expected: agent listed, state `waiting`. (Kick a manual run: `launchctl kickstart gui/$(id -u)/com.lordwilson.msb-backup`.)

- [ ] **Step 5: Confirm restore drill + commit**

Run a real drill (copy the newest backup out, verify it round-trips) then commit:
```bash
/opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest tests/ops -q
git add scripts/backup.sh scripts/launchd/com.lordwilson.msb-backup.plist docs/blueprints/2026-08-11-adaptive-build-environment.md
git commit -m "feat(ops): daily launchd backup agent + retention"
```

---

## Self-Review

- **Spec coverage (Phase 0 protect items):** backup + tested restore ✅ (Tasks 1–3); retention ✅ (Task 4); scheduling ✅ (Task 5). `.gitignore` for churn — **intentionally skipped**: verified 2026-08-11 that `.gitignore` deliberately keeps `hygiene_aggregate.json` ("keep aggregate + gate"), so no change needed. Path/config portability and reproducible-rebuild (`Dockerfile`/`setup.sh`) and model-provisioning are **carried to a separate Phase 0B plan** with the autonomy brakes — they're a distinct subsystem and this plan already produces working, testable software on its own.
- **Placeholder scan:** none — every step has real code/commands.
- **Type consistency:** `create_backup`/`restore_backup`/`verify_backup`/`list_backups`/`prune_backups`/`default_paths` signatures are consistent across Tasks 2–5; `BackupManifest` fields match their uses.

## Not in this plan (explicit)

- The four autonomy **brakes** (Ouroboros governor, budget/rate caps, approval queue, kill switch) → **Phase 0B plan**.
- Reproducible rebuild (`Dockerfile`/`setup.sh`) + model provisioning → **Phase 0B plan**.
- The Cockpit window → **Phase 1 plan**.
