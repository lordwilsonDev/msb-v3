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
