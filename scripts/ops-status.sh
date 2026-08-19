#!/usr/bin/env bash
set -euo pipefail

# ops-status.sh — one-glance status of the msb-v3 ops layer.
#
# Shows every agent the backup watchdog tracks (last exit, run count,
# schedule), the disk state vs the disk-health thresholds, backup counts
# (DB snapshots + vault index per label), and the tail line of the key
# logs. Read-only; safe to run anytime.
#
# Run: bash scripts/ops-status.sh

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"
UID_NUM="$(id -u)"
VAULT="${MSB_VAULT:-$HOME/Documents/Vault}"
DB_DEST="$HOME/msb-backups/msb-v3"

# plist schedule extractor (kept in a file: python code with parens cannot
# live inside a $(...) substitution).
SCHED_PY="$(mktemp /tmp/ops-status-sched-XXXXXX.py)"
trap 'rm -f "$SCHED_PY"' EXIT
cat > "$SCHED_PY" <<'PYEOF'
import plistlib, sys
try:
    with open(sys.argv[1], "rb") as f:
        d = plistlib.load(f)
except Exception:
    print("n/a")
    sys.exit(0)
sci = d.get("StartCalendarInterval") or {}
si = d.get("StartInterval")
if si:
    print("every %ss" % si)
elif sci:
    wd = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}.get(sci.get("Weekday"), "daily")
    print("%s %02d:%02d" % (wd, sci.get("Hour", 0), sci.get("Minute", 0)))
elif d.get("KeepAlive"):
    print("KeepAlive")
else:
    print("manual/other")
PYEOF

echo "=== msb-v3 ops status — $(date '+%F %T') ==="

echo
echo "AGENTS (watchdog list):"
# Keep in sync with backup-watchdog.sh: derive the label list from it.
for label in $(grep -o 'com\.lordwilson\.[a-z0-9.-]*' "$REPO/scripts/backup-watchdog.sh" | grep -v '\.plist$' | sort -u); do
  printout="$(launchctl print "gui/$UID_NUM/$label" 2>/dev/null || true)"
  if [ -z "$printout" ]; then
    printf '  %-38s not loaded\n' "$label"
    continue
  fi
  state="$(sed -n 's/^[[:space:]]*state = //p' <<<"$printout" | head -1)"
  runs="$(sed -n 's/^[[:space:]]*runs = //p' <<<"$printout" | head -1)"
  exitcode="$(sed -n 's/^[[:space:]]*last exit code = //p' <<<"$printout" | head -1)"
  [ -n "${runs:-}" ] || runs="-"
  [ -n "${exitcode:-}" ] || exitcode="(never)"
  sched="$("$PY" "$SCHED_PY" "$HOME/Library/LaunchAgents/$label.plist" 2>/dev/null || echo n/a)"
  printf '  %-38s %-12s runs=%-4s last=%-8s %s\n' "$label" "$state" "$runs" "$exitcode" "$sched"
done

echo
echo "DISK:"
df -h /System/Volumes/Data 2>/dev/null | tail -1 | awk '{printf "  %s total, %s used, %s free (%s) on %s\n", $2, $3, $4, $5, $NF}'
warn="$(grep -o 'WARN_PCT:-[0-9]*' "$REPO/scripts/disk-health.sh" | grep -o '[0-9]*' | head -1)"
crit="$(grep -o 'CRIT_PCT:-[0-9]*' "$REPO/scripts/disk-health.sh" | grep -o '[0-9]*' | head -1)"
echo "  disk-health thresholds: warn ${warn}% / crit ${crit}% (Sun 06:45)"

echo
echo "BACKUPS:"
if [ -d "$DB_DEST" ]; then
  n_db="$(ls -d "$DB_DEST"/* 2>/dev/null | wc -l | tr -d ' ')"
  size_db="$(du -sh "$DB_DEST" 2>/dev/null | cut -f1)"
  echo "  db: $n_db snapshot(s), $size_db (keep 7, daily 03:00)"
fi
if [ -f "$VAULT/Backups/.backup-index" ]; then
  echo "  vault index (per label):"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    p="${line%%|*}"
    label="$(basename "$p" | sed -E 's/-[A-Z0-9]{11}$//')"
    echo "    $label"
  done < "$VAULT/Backups/.backup-index" | sort | uniq -c | awk '{printf "    %-12s %s snapshot(s)\n", $2, $1}'
fi

echo
echo "LICENSE:"
"$REPO/scripts/verify-license.sh" 2>&1 | sed 's/^/  /' || true


echo
echo "LOGS (last line):"
for f in disk-health backup-watchdog cache-trim rotate-logs db-restore-drill vault-backup; do
  logf="$REPO/logs/$f.log"
  [ -f "$logf" ] || continue
  printf '  %-20s %s\n' "$f:" "$(tail -1 "$logf")"
done
