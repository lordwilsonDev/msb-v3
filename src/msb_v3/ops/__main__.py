"""CLI: python -m msb_v3.ops backup [--keep N] | restore <timestamp|latest>"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from msb_v3.ops.backup import (
    create_backup,
    restore_backup,
    list_backups,
    prune_backups,
    default_paths,
)


def main() -> None:
    data_dir, storage_dir, dest_root = default_paths()
    ap = argparse.ArgumentParser(prog="msb_v3.ops")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--keep", type=int, default=14)
    r = sub.add_parser("restore")
    r.add_argument("which")
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
