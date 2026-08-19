#!/usr/bin/env bash
set -euo pipefail

# db-restore-drill.sh — prove the latest DB backup actually restores.
#
# Restores the newest ~/msb-backups/msb-v3 snapshot into a temp dir (via the
# same restore_backup path a real recovery would use), checksum-verifies it,
# and runs PRAGMA integrity_check on every restored SQLite db. Any failure
# exits non-zero so the backup watchdog alerts. Driven weekly (Sunday 06:30)
# by com.lordwilson.db-restore-drill.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"
LOG="$REPO/logs/db-restore-drill.log"
mkdir -p "$REPO/logs"
log() { echo "[db-restore-drill] $(date '+%F %T') $*" | tee -a "$LOG"; }

tmp="$(mktemp -d /tmp/db-restore-drill-XXXXXX)"
trap 'rm -rf "$tmp"' EXIT

if TMP_DIR="$tmp" REPO="$REPO" "$PY" - <<'PYEOF' 2>"$tmp/drill.err"
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.environ["REPO"] + "/src")

from msb_v3.ops.backup import list_backups, restore_backup, verify_backup

dest = Path.home() / "msb-backups" / "msb-v3"
backups = list_backups(dest)
if not backups:
    print("NO-BACKUPS", file=sys.stderr)
    sys.exit(2)
latest = backups[-1]

if not verify_backup(latest):
    print(f"checksum failure: {latest}", file=sys.stderr)
    sys.exit(3)

tmp = Path(os.environ["TMP_DIR"])
restore_backup(latest, tmp / "data", tmp / "storage")

dbs = sorted((tmp / "data").rglob("*.db"))
for db in dbs:
    con = sqlite3.connect(db)
    try:
        rows = con.execute("PRAGMA integrity_check").fetchall()
    finally:
        con.close()
    if not rows or any(r[0] != "ok" for r in rows):
        print(f"integrity failure: {db}", file=sys.stderr)
        sys.exit(4)

print(f"RESTORE-DRILL-OK src={latest.name} dbs={len(dbs)} storage_files={sum(1 for _ in (tmp / 'storage').rglob('*') if _.is_file())}")
PYEOF
then
  log "OK"
else
  rc=$?
  log "FAILED (exit $rc)"
  if [ -s "$tmp/drill.err" ]; then
    tail -5 "$tmp/drill.err" | while IFS= read -r l; do log "  $l"; done
  fi
  exit "$rc"
fi
