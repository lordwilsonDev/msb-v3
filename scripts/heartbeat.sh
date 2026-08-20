#!/usr/bin/env bash
set -euo pipefail

# heartbeat.sh — liveness + off-machine copy of the ops trail.
#
# Appends a timestamped liveness line and a dated state snapshot (ops
# status, disk, audit summary) plus the repo's audit/ evidence dir, to an
# OFF-MACHINE sink, chosen from:
#
#   1. MSB_HEARTBEAT_RCLONE_REMOTE  — an rclone remote directory (e.g.
#      gdrive:msb-v3/heartbeat). Content is staged locally (MSB_HEARTBEAT_STAGE,
#      default ~/msb-backups/heartbeat) and pushed with `rclone copy` — no
#      external hardware needed, uses the same gdrive remote the notary uses.
#   2. MSB_HEARTBEAT_DIR             — an EXTERNAL volume mount (e.g.
#      /Volumes/BackupDrive) — a second copy on hardware this machine does
#      not control.
#
# No sink configured or mounted -> logs a note and exits 0 (this is a
# scheduled agent; an absent sink is a config state, not a failure).
# A CONFIGURED rclone remote that fails to push -> exits 1 (loud): once you
# declare an off-machine sink, losing it must alert the watchdog.
#
# Driven daily 12:00 by com.lordwilson.heartbeat.
#
# Overrides: MSB_HEARTBEAT_RCLONE_REMOTE, MSB_HEARTBEAT_DIR,
# MSB_HEARTBEAT_STAGE, MSB_HEARTBEAT_LOG, MSB_AUDIT_DIR.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# launchd runs scripts with a minimal PATH; homebrew tools (rclone) live
# outside it — same fix as notarize_chain_anchor.sh.
export PATH="/opt/homebrew/bin:/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
RCLONE="${MSB_HEARTBEAT_RCLONE_REMOTE:-}"
DIR="${MSB_HEARTBEAT_DIR:-}"
STAGE="${MSB_HEARTBEAT_STAGE:-$HOME/msb-backups/heartbeat}"
LOG="${MSB_HEARTBEAT_LOG:-$REPO/logs/heartbeat.log}"
AUDIT_DIR="${MSB_AUDIT_DIR:-$REPO/audit}"
mkdir -p "$(dirname "$LOG")"
log() { echo "[heartbeat] $(date '+%F %T') $*" | tee -a "$LOG"; }

if [ -z "$RCLONE" ] && [ -z "$DIR" ]; then
  log "no heartbeat sink (MSB_HEARTBEAT_RCLONE_REMOTE / MSB_HEARTBEAT_DIR unset) — skipping (exit 0)"
  exit 0
fi

if [ -n "$RCLONE" ]; then
  # rclone remote path: stage locally, push off-machine below.
  DST="$STAGE"
  mkdir -p "$DST"
elif [ ! -d "$DIR" ]; then
  log "no heartbeat volume (MSB_HEARTBEAT_DIR=${DIR:-unset}) — skipping (exit 0)"
  exit 0
else
  DST="$DIR/msb-v3"
  mkdir -p "$DST"
fi

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

# 4. rclone push leg — the off-machine copy when a remote is configured.
if [ -n "$RCLONE" ]; then
  if rclone copy "$DST/" "$RCLONE/" 2>>"$LOG"; then
    log "rclone push OK: $DST -> $RCLONE"
  else
    log "FAIL: rclone push failed: $RCLONE"
    exit 1
  fi
fi

log "heartbeat recorded ($(wc -l < "$DST/heartbeat.log") lines on $DST)"
