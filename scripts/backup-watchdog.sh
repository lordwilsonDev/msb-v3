#!/usr/bin/env bash
set -euo pipefail

# backup-watchdog.sh — alert when any backup LaunchAgent's latest run failed.
#
# Polls launchctl for each backup agent's completed-run count and exit code.
# When a new run finishes with a non-zero exit it fires one macOS
# notification (plus an alert-log line); the alert state clears when the
# agent's next run succeeds, so a repeated failure alerts only once.
#
# Driven every 15 minutes by com.lordwilson.backup-watchdog
# (template: scripts/launchd/com.lordwilson.backup-watchdog.plist).
#
# Overrides (testing): MSB_WATCHDOG_STATE, MSB_WATCHDOG_LOG,
# MSB_WATCHDOG_AGENTS (newline-separated "label|description|log-hint").

UID_NUM="$(id -u)"
STATE="${MSB_WATCHDOG_STATE:-$HOME/.backup-watchdog-state}"
ALERT_LOG="${MSB_WATCHDOG_LOG:-/Users/lordwilson/msb-v3/logs/backup-watchdog.log}"

# NB: run under macOS /bin/bash 3.2 — no mapfile, no associative arrays.
AGENTS=(
  "com.lordwilson.msb-backup|DB backup|msb-v3/logs/backup.err"
  "com.lordwilson.msb-vault-backup|msb-v3 code backup|msb-v3/logs/vault-backup.log"
  "com.lordwilson.dsh-vault-backup|deepseek-harness code backup|deepseek-harness/logs/vault-backup.log"
)
if [ -n "${MSB_WATCHDOG_AGENTS:-}" ]; then
  AGENTS=()
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    AGENTS+=("$line")
  done <<<"$MSB_WATCHDOG_AGENTS"
fi

[ -f "$STATE" ] || : > "$STATE"

alert() { # label desc exitcode loghint
  local label="$1" desc="$2" code="$3" loghint="$4"
  local msg="Backup agent $label ($desc) failed with exit code $code. Log: $loghint"
  osascript -e "display notification \"${msg//\"/\'}\" with title \"Backup failure\"" >/dev/null 2>&1 || true
  echo "[$(date '+%F %T')] ALERT: $msg" >> "$ALERT_LOG"
}

for entry in "${AGENTS[@]}"; do
  label="${entry%%|*}"
  rest="${entry#*|}"
  desc="${rest%%|*}"
  loghint="${rest#*|}"

  printout="$(launchctl print "gui/$UID_NUM/$label" 2>/dev/null)" || continue
  runs="$(sed -n 's/^\truns = //p' <<<"$printout" | head -1)"
  exitcode="$(sed -n 's/^\tlast exit code = //p' <<<"$printout" | head -1)"
  [ -n "${runs:-}" ] || continue
  [ "${exitcode:-}" != "(never exited)" ] || continue  # no completed run yet

  prev="$(grep "^$label|" "$STATE" || true)"
  prev_runs="$(cut -d'|' -f2 <<<"$prev" || true)"
  prev_alerted="$(cut -d'|' -f4 <<<"$prev" || true)"
  [ -n "${prev_runs:-}" ] || prev_runs=0
  [ -n "${prev_alerted:-}" ] || prev_alerted=0

  # Only act when a NEW run has completed since the last check.
  if [ "$runs" -gt "$prev_runs" ]; then
    alerted=0
    if [ "$exitcode" != "0" ]; then
      alerted=1
      if [ "$prev_alerted" != "1" ]; then
        alert "$label" "$desc" "$exitcode" "$loghint"
      fi
    fi
    # rewrite this agent's state line (drop old, append new)
    grep -v "^$label|" "$STATE" > "$STATE.tmp" || true
    printf '%s|%s|%s|%s\n' "$label" "$runs" "$exitcode" "$alerted" >> "$STATE.tmp"
    mv "$STATE.tmp" "$STATE"
  fi
done
