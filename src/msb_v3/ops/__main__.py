"""CLI: python -m msb_v3.ops backup [--keep N] | restore <timestamp|latest>"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from msb_v3.ops.backup import (
    create_backup,
    default_notary_log,
    default_paths,
    list_backups,
    prune_backups,
    restore_backup,
    verify_backup,
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
        notary = default_notary_log()
        m = create_backup(data_dir, storage_dir, dest_root, timestamp=ts, notary_log=notary)
        pruned = prune_backups(dest_root, keep=args.keep)
        # Checksum-verify the snapshot we just wrote: a torn Qdrant storage/
        # copy (copied live while Qdrant runs) would otherwise pass silently
        # and only surface at restore time. Fail the run so the watchdog
        # alerts; the snapshot is left in place for inspection.
        if not verify_backup(m.path):
            print(f"[backup] FAILED checksum verification: {m.path}", file=sys.stderr)
            sys.exit(1)
        notary_in = (m.path / "notary" / notary.name).exists()
        print(f"[backup] {m.path}  dbs={m.db_count}  pruned={len(pruned)}  notary={notary_in}  verified=ok")
    elif args.cmd == "restore":
        backups = list_backups(dest_root)
        if not backups:
            raise SystemExit("no backups found")
        target = backups[-1] if args.which == "latest" else dest_root / args.which
        restore_backup(target, data_dir, storage_dir, notary_dest=default_notary_log())
        print(f"[restore] restored from {target}")


if __name__ == "__main__":
    main()
