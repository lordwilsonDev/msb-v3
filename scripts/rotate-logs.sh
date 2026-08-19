#!/usr/bin/env bash
set -euo pipefail

# rotate-logs.sh — cap the unbounded launchd-captured logs.
#
# StandardOut/StandardErrorPath files (uvicorn, qdrant) are held open by
# launchd, so mv-based rotation would orphan the live fd. Copy-then-truncate
# works on the same inode: cp the current file to .1, then truncate the
# live file in place; older copies shift up. Keeps SIZE_CAP of history per
# file, drops the rest. Driven daily (06:00) by com.lordwilson.rotate-logs.
#
# Overrides: MSB_ROTATE_CAP (bytes, default 5M), MSB_ROTATE_KEEP (copies, 3)

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAP="${MSB_ROTATE_CAP:-$((5 * 1024 * 1024))}"
KEEP="${MSB_ROTATE_KEEP:-3}"
LOG="$REPO/logs/rotate-logs.log"
mkdir -p "$(dirname "$LOG")"
log() { echo "[rotate-logs] $(date '+%F %T') $*" | tee -a "$LOG"; }

# Long-lived logs across the agents (vault-backup logs are tiny; include
# them anyway so they never become a problem). Repo-relative paths keep
# this portable; the dsh log lives in a sibling repo under $HOME.
TARGETS=(
  "$REPO/logs/gateway.out.log"
  "$REPO/logs/gateway.err.log"
  "$REPO/logs/qdrant.log"
  "$REPO/logs/qdrant-launchd.out.log"
  "$REPO/logs/audit.jsonl"
  "$REPO/logs/backup.log"
  "$REPO/logs/backup.err"
  "$REPO/logs/vault-backup.log"
  "$REPO/logs/backup-watchdog.log"
  "$REPO/logs/db-restore-drill.log"
  "$REPO/logs/disk-health.log"
  "$HOME/deepseek-harness/logs/vault-backup.log"
)

rotated=0
for f in "${TARGETS[@]}"; do
  [ -f "$f" ] || continue
  size="$(stat -f%z "$f" 2>/dev/null || echo 0)"
  [ "$size" -gt "$CAP" ] || continue
  # shift history: .KEEP-1 -> ... -> .1 -> live (cp + truncate)
  for ((i = KEEP - 1; i >= 1; i--)); do
    [ -f "$f.$i" ] && mv -f "$f.$i" "$f.$((i + 1))"
  done
  cp -f "$f" "$f.1"
  : > "$f"
  rotated=$((rotated + 1))
  log "rotated $f ($(( size / 1024 / 1024 ))M -> $f.1)"
done

[ "$rotated" -eq 0 ] && log "nothing to rotate (all under cap)"
log "done ($rotated rotated)"
