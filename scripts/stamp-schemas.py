#!/usr/bin/env python3
"""Stamp / audit SQLite schema versions across the data directory.

PRODUCTION-CLOSURE-001 H9. The migration framework (``db/migrations.py``)
shipped but was never wired to the live databases — they all reported
``_schema_version`` absent. This walks ``data/`` and applies the baseline
migration so every DB has a version floor for future migrations.

    python scripts/stamp-schemas.py            # stamp anything below v1
    python scripts/stamp-schemas.py --check    # report only; exit 1 on drift

``--check`` is wired into scripts/ops-audit.sh so drift (a new unstamped DB)
is caught in the Sunday cascade.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

from msb_v3.db.migrations import list_versions, stamp_all_db  # noqa: E402


def _data_dir() -> Path:
    try:
        from msb_v3.core.config import settings

        return Path(settings.db_path).resolve().parent
    except Exception:
        return (Path(__file__).resolve().parents[1] / "data").resolve()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="report only; exit 1 on drift")
    ap.add_argument("--data-dir", default=None, help="override the data directory")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).resolve() if args.data_dir else _data_dir()
    if not data_dir.is_dir():
        print(f"[stamp-schemas] data dir not found: {data_dir}", file=sys.stderr)
        return 1

    if args.check:
        versions = list_versions(data_dir)
        drift = {rel: v for rel, v in versions.items() if v < 1}
        for rel, v in sorted(versions.items()):
            mark = "  DRIFT" if v < 1 else ""
            print(f"  v{v:<3} {rel}{mark}")
        if drift:
            print(
                f"[stamp-schemas] FAIL: {len(drift)} unstamped DB(s) — run "
                f"`python scripts/stamp-schemas.py` to fix",
                file=sys.stderr,
            )
            return 1
        print(f"[stamp-schemas] OK: {len(versions)} DB(s), all >= v1")
        return 0

    before = list_versions(data_dir)
    result = stamp_all_db(data_dir)
    failed = [rel for rel, v in result.items() if v < 0]
    stamped = [rel for rel, v in result.items() if before.get(rel, 0) < 1 <= v]
    for rel in sorted(result):
        print(f"  {before.get(rel, 0)} -> {result[rel]}  {rel}")
    print(
        f"[stamp-schemas] {len(result)} DB(s): {len(stamped)} newly stamped, "
        f"{len(failed)} failed"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
