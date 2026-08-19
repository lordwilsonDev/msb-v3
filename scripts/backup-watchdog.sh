#!/usr/bin/env bash
set -euo pipefail

# backup-watchdog.sh — alert when any backup LaunchAgent's latest run failed.
#
# Polls launchctl for each backup agent's completed-run count and exit code.
# When a run finishes with a non-zero exit it fires one macOS notification
# (plus an alert-log line); the alert state clears when the agent's next run
# succeeds, so a repeated failure alerts only once.
#
# In-flight runs are skipped for scheduled agents: launchd increments the
# run count when a job STARTS, with "last exit code" still showing the
# PREVIOUS run — acting then would fire a false alert (saw it live: the
# ops-audit kick alerted on its own still-running suite). KeepAlive agents
# (server/qdrant) are always "running", so for them the last completed
# exit code is exactly the signal and is processed normally.
#
# Driven every 15 minutes by com.lordwilson.backup-watchdog
# (template: scripts/launchd/com.lordwilson.backup-watchdog.plist).
#
# Overrides (testing): MSB_WATCHDOG_STATE, MSB_WATCHDOG_LOG,
# MSB_WATCHDOG_AGENTS (newline-separated "label|description|log-hint").

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UID_NUM="$(id -u)"
STATE="${MSB_WATCHDOG_STATE:-$HOME/.backup-watchdog-state}"
ALERT_LOG="${MSB_WATCHDOG_LOG:-$REPO/logs/backup-watchdog.log}"
# KeepAlive agents (server/qdrant) never exit 0 while healthy, so their
# alert can't clear on success; re-arm instead after this many seconds so a
# later crash alerts again (backup agents clear via exit 0 long before).
REARM_SECONDS="${MSB_WATCHDOG_REARM:-21600}"

# NB: run under macOS /bin/bash 3.2 — no mapfile, no associative arrays.
AGENTS=(
  "com.lordwilson.msb-backup|DB backup|msb-v3/logs/backup.err"
  "com.lordwilson.msb-vault-backup|msb-v3 code backup|msb-v3/logs/vault-backup.log"
  "com.lordwilson.dsh-vault-backup|deepseek-harness code backup|deepseek-harness/logs/vault-backup.log"
  "com.lordwilson.db-restore-drill|DB restore drill|msb-v3/logs/db-restore-drill.err"
  "com.lordwilson.rotate-logs|log rotation|msb-v3/logs/rotate-logs.err"
  "com.lordwilson.cache-trim|cache trim|msb-v3/logs/cache-trim.err"
  "com.lordwilson.disk-health|disk health check|msb-v3/logs/disk-health.err"
  "com.lordwilson.ops-audit|ops audit|msb-v3/logs/ops-audit.err"
  "com.lordwilson.heartbeat|heartbeat (off-machine copy)|msb-v3/logs/heartbeat.err"
  "com.lordwilson.replicate|secondary replication|msb-v3/logs/replicate.err"
  "com.lordwilson.msb-v3|msb-v3 server|msb-v3/logs/gateway.err.log"
  "com.lordwilson.qdrant|Qdrant|msb-v3/logs/qdrant.log"
)
if [ -n "${MSB_WATCHDOG_AGENTS:-}" ]; then
  AGENTS=()
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    AGENTS+=("$line")
  done <<<"$MSB_WATCHDOG_AGENTS"
fi

# Agents that never exit 0 while healthy (their alert clears via REARM timer).
KEEPALIVE_LABELS="com.lordwilson.msb-v3 com.lordwilson.qdrant"

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
  state="$(sed -n 's/^\tstate = //p' <<<"$printout" | head -1)"
  runs="$(sed -n 's/^\truns = //p' <<<"$printout" | head -1)"
  exitcode="$(sed -n 's/^\tlast exit code = //p' <<<"$printout" | head -1)"
  [ -n "${runs:-}" ] || continue
  [ "${exitcode:-}" != "(never exited)" ] || continue  # no completed run yet

  # Skip in-flight runs for scheduled agents (see header). KeepAlive agents
  # are always "running" — their last completed exit code is the signal.
  keepalive=0
  case " $KEEPALIVE_LABELS " in *" $label "*) keepalive=1;; esac
  if [ "$state" = "running" ] && [ "$keepalive" != "1" ]; then
    continue
  fi

  prev="$(grep "^$label|" "$STATE" || true)"
  prev_runs="$(cut -d'|' -f2 <<<"$prev" || true)"
  prev_exit="$(cut -d'|' -f3 <<<"$prev" || true)"
  prev_alerted="$(cut -d'|' -f4 <<<"$prev" || true)"
  prev_ts="$(cut -d'|' -f5 <<<"$prev" || true)"
  [ -n "${prev_runs:-}" ] || prev_runs=0
  [ -n "${prev_exit:-}" ] || prev_exit=""
  [ -n "${prev_alerted:-}" ] || prev_alerted=0
  [ -n "${prev_ts:-}" ] || prev_ts=0

  # Act when a run completed since the last check (run count increased) OR
  # the exit code changed for the same run count — a completed run reporting
  # its real code after an in-flight skip (clears stale alerts either way).
  if [ "$runs" -ne "$prev_runs" ] || [ "$exitcode" != "$prev_exit" ]; then
    alerted="$prev_alerted"
    ts="$prev_ts"
    if [ "$exitcode" != "0" ]; then
      now="$(date +%s)"
      if [ "$alerted" != "1" ] || [ "$ts" -eq 0 ] || [ "$(( now - ts ))" -gt "$REARM_SECONDS" ]; then
        alert "$label" "$desc" "$exitcode" "$loghint"
        alerted=1
        ts="$now"
      fi
    else
      alerted=0
      ts=0
    fi
    # rewrite this agent's state line (drop old, append new)
    grep -v "^$label|" "$STATE" > "$STATE.tmp" || true
    printf '%s|%s|%s|%s|%s\n' "$label" "$runs" "$exitcode" "$alerted" "$ts" >> "$STATE.tmp"
    mv "$STATE.tmp" "$STATE"
  fi
done
