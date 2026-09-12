#!/usr/bin/env bash
set -euo pipefail

# housekeeping.sh — single entry point for the three pure-housekeeping ops
# scripts (rotate-logs, cache-trim, disk-health).
#
# These three used to be three independent LaunchAgents. The forensic audit
# (2026-09-11) flagged them as the one part of the ops layer with no reason
# to be three separate failure domains — unlike the backup/notary/qdrant
# agents (a watchdog folded into the thing it watches can't report the thing
# being dead), these are just chores with no cross-monitoring need. Folding
# them into one agent means one path/schedule to keep correct instead of
# three (see the Sep-2026 "16 launchd jobs pointed at a moved repo" incident).
#
# rotate-logs runs every day (unchanged cadence, just a new trigger time).
# cache-trim then disk-health run Sunday only, in that order, so disk-health
# measures post-trim usage — exactly the ordering the three scripts had
# before, just inside one process instead of three launchd fires.
#
# Driven daily at 06:40 by com.lordwilson.housekeeping (template:
# scripts/launchd/com.lordwilson.housekeeping.plist) — 06:40 was cache-trim's
# old Sunday slot, chosen so the Sunday cascade (db-restore-drill 06:30 ->
# housekeeping 06:40 -> ops-audit 06:50) is unchanged; only rotate-logs'
# Mon-Sat time moves from 06:00 to 06:40, which nothing else depends on.
#
# Override: MSB_HOUSEKEEPING_RUN_WEEKLY forces the Sunday-only steps on/off
# (1/0) for testing instead of reading the real day-of-week.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${MSB_HOUSEKEEPING_LOG:-$REPO/logs/housekeeping.log}"
mkdir -p "$(dirname "$LOG")"
log() { echo "[housekeeping] $(date '+%F %T') $*" | tee -a "$LOG"; }

if [ -n "${MSB_HOUSEKEEPING_RUN_WEEKLY:-}" ]; then
  run_weekly="$MSB_HOUSEKEEPING_RUN_WEEKLY"
else
  # date +%w: 0 = Sunday
  [ "$(date +%w)" -eq 0 ] && run_weekly=1 || run_weekly=0
fi

log "start (run_weekly=$run_weekly)"

bash "$REPO/scripts/rotate-logs.sh"

if [ "$run_weekly" -eq 1 ]; then
  bash "$REPO/scripts/cache-trim.sh"
  bash "$REPO/scripts/disk-health.sh"
else
  log "skipping cache-trim + disk-health (not Sunday)"
fi

log "done"
