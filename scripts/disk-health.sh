#!/usr/bin/env bash
set -euo pipefail

# disk-health.sh — weekly disk-usage health check that alerts before the disk
# fills.
#
# Two alert conditions:
#   1. Immediate: used% >= MSB_DISK_WARN_PCT (default 85), escalated to
#      CRITICAL at MSB_DISK_CRIT_PCT (default 92).
#   2. Trend: with a few weeks of history in the state file, projects when
#      free space hits zero at the current consumption rate and alerts when
#      the projected time-to-full drops below MSB_DISK_HORIZON_DAYS (default
#      14). This is the one that catches slow leaks (log growth, backup
#      accumulation) before they bite.
#
# Alerts once per episode (macOS notification + line in the log); the
# episode clears when usage/projection recover, and a WARN -> CRIT jump
# re-alerts. Every run writes a breadcrumb line to the log.
#
# Driven weekly (Sun 06:45) by com.lordwilson.disk-health
# (template: scripts/launchd/com.lordwilson.disk-health.plist).
#
# Overrides (testing): MSB_DISK_WARN_PCT, MSB_DISK_CRIT_PCT,
# MSB_DISK_HORIZON_DAYS, MSB_DISK_STATE, MSB_DISK_LOG, MSB_DISK_MOUNT.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WARN="${MSB_DISK_WARN_PCT:-85}"
CRIT="${MSB_DISK_CRIT_PCT:-92}"
HORIZON="${MSB_DISK_HORIZON_DAYS:-14}"
MOUNT="${MSB_DISK_MOUNT:-/System/Volumes/Data}"
STATE="${MSB_DISK_STATE:-$HOME/.disk-health-state}"
LOG="${MSB_DISK_LOG:-$REPO/logs/disk-health.log}"
KEEP_SAMPLES=16

mkdir -p "$(dirname "$LOG")"
log() { echo "[disk-health] $(date '+%F %T') $*" | tee -a "$LOG"; }

# --- current usage -----------------------------------------------------------
# df -k row: fs size(kB) used(kB) avail(kB) cap% ... -> $1 $2 $3 $4
read -r _ size_kb used_kb avail_kb _rest < <(df -k "$MOUNT" | tail -1) || {
  log "ERROR: df failed on $MOUNT"
  exit 1
}
used_pct=$(( (size_kb - avail_kb) * 100 / size_kb ))
free_gb=$(( avail_kb / 1048576 ))

# --- history / trend ----------------------------------------------------------
[ -f "$STATE" ] || printf 'EPISODE|0|0\n' > "$STATE"
first="$(head -1 "$STATE")"
alerted="$(printf '%s' "$first" | cut -d'|' -f2)"
sev="$(printf '%s' "$first" | cut -d'|' -f3)"
[ -n "$alerted" ] || alerted=0
[ -n "$sev" ] || sev=0

now_epoch="$(date +%s)"
stamp="$(date '+%Y-%m-%d %H:%M')"
days_to_full=-1
rate_mb_day=0
old_epoch=0
old_avail=0
n=0
# Use the oldest sample for a max-span average consumption rate.
while IFS= read -r s; do
  [ -n "$s" ] || continue
  if [ "$n" -eq 0 ]; then
    old_epoch="$(date -j -f '%Y-%m-%d %H:%M' "${s%%|*}" '+%s' 2>/dev/null || echo 0)"
    old_avail="${s#*|}"
  fi
  n=$((n + 1))
done < <(tail -n +2 "$STATE")

if [ "$n" -ge 1 ] && [ "$old_epoch" -gt 0 ]; then
  span=$(( (now_epoch - old_epoch) / 86400 ))
  if [ "$span" -ge 1 ]; then
    rate_kb_day=$(( (avail_kb - old_avail) / span ))
    if [ "$rate_kb_day" -lt 0 ]; then
      days_to_full=$(( avail_kb / (-rate_kb_day) ))
      rate_mb_day=$(( (-rate_kb_day) / 1024 ))
    fi
  fi
fi

# --- evaluate conditions ------------------------------------------------------
reasons=""
newsev=0
if [ "$used_pct" -ge "$CRIT" ]; then
  reasons="CRITICAL: ${used_pct}% used (${free_gb} GB free)"
  newsev=2
elif [ "$used_pct" -ge "$WARN" ]; then
  reasons="WARNING: ${used_pct}% used (${free_gb} GB free)"
  newsev=1
fi
if [ "$days_to_full" -ge 0 ] && [ "$days_to_full" -le "$HORIZON" ]; then
  [ -n "$reasons" ] && reasons="${reasons}; "
  reasons="${reasons}projected full in ~${days_to_full}d at ${rate_mb_day} MB/day"
  [ "$newsev" -lt 1 ] && newsev=1
fi

# --- alert (once per episode, re-alert on escalation) --------------------------
if [ -n "$reasons" ]; then
  if [ "$alerted" -eq 0 ] || [ "$newsev" -gt "$sev" ]; then
    msg="Disk at ${used_pct}% (${free_gb} GB free): ${reasons}"
    osascript -e "display notification \"${msg//\"/\'}\" with title \"Disk usage warning\"" >/dev/null 2>&1 || true
    echo "[$(date '+%F %T')] ALERT: $msg" >> "$LOG"
    alerted=1
    sev=$newsev
  fi
else
  alerted=0
  sev=0
fi

# --- persist state -------------------------------------------------------------
{
  printf 'EPISODE|%s|%s\n' "$alerted" "$sev"
  tail -n +2 "$STATE" | tail -n "$((KEEP_SAMPLES - 1))"
  printf '%s|%s\n' "$stamp" "$avail_kb"
} > "$STATE.tmp"
mv "$STATE.tmp" "$STATE"

log "usage=${used_pct}% free=${free_gb}G trend=${rate_mb_day}MB/day days_to_full=${days_to_full} alert=${alerted}"
