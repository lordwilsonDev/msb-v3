#!/usr/bin/env bash
set -euo pipefail

# heartbeat.sh — liveness + off-machine copy of the ops trail.
#
# Appends a timestamped liveness line and a dated state snapshot (ops
# status, disk, audit summary) to an EXTERNAL volume (set MSB_HEARTBEAT_DIR
# to the mount point, e.g. /Volumes/BackupDrive), and rsyncs the repo's
# audit/ evidence dir there — so a second copy of the trail exists on
# hardware this machine does not control.
#
# No volume configured or mounted -> logs a note and exits 0 (this is a
# scheduled agent; an absent volume is a config state, not a failure).
#
# Driven daily 12:00 by com.lordwilson.heartbeat.
#
# Overrides: MSB_HEARTBEAT_DIR, MSB_HEARTBEAT_LOG, MSB_AUDIT_DIR.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="${MSB_HEARTBEAT_DIR:-}"
LOG="${MSB_HEARTBEAT_LOG:-$REPO/logs/heartbeat.log}"
AUDIT_DIR="${MSB_AUDIT_DIR:-$REPO/audit}"
mkdir -p "$(dirname "$LOG")"
log() { echo "[heartbeat] $(date '+%F %T') $*" | tee -a "$LOG"; }

if [ -z "$DIR" ] || [ ! -d "$DIR" ]; then
  log "no heartbeat volume (MSB_HEARTBEAT_DIR=${DIR:-unset}) — skipping (exit 0)"
  exit 0
fi

DST="$DIR/msb-v3"
mkdir -p "$DST"
now="$(date '+%F %T')"
host="$(hostname -s 2>/dev/null || echo unknown)"
branch="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
head_short="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"

# 1. liveness line
printf '%s|%s|%s|%s|%s\n' "$now" "$host" "$REPO" "$branch" "$head_short" >> "$DST/heartbeat.log"

# 2. dated state snapshot
{
  echo "# msb-v3 heartbeat snapshot — $now"
  echo
  echo "host=$host repo=$REPO branch=$branch head=$head_short"
  echo
  echo "## Ops status"
  echo '```'
  bash "$REPO/scripts/ops-status.sh" 2>&1 | head -80 || echo "(ops-status unavailable)"
  echo '```'
  echo
  echo "## Disk"
  echo '```'
  df -h / | tail -1
  echo '```'
  echo
  echo "## Last audit summary"
  echo '```'
  tail -5 "$REPO/logs/ops-audit.log" 2>/dev/null || echo "(no audit log yet)"
  echo '```'
} > "$DST/snapshot-$(date '+%Y-%m-%d').md"
cp -f "$DST/snapshot-$(date '+%Y-%m-%d').md" "$DST/snapshot-latest.md" 2>/dev/null || true

# 3. rsync the evidence dir (audit reports + anything tracked under audit/)
if [ -d "$AUDIT_DIR" ]; then
  rsync -a --delete "$AUDIT_DIR/" "$DST/audit/" 2>/dev/null \
    && log "audit/ copied to $DST/audit/" \
    || log "WARNING: audit rsync failed"
fi

log "heartbeat recorded ($(wc -l < "$DST/heartbeat.log") lines on $DIR)"
