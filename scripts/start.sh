#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/lordwilson/msb-v3"
PIDFILE="$REPO/.artifacts/msb-v3.pid"
LOGFILE="$REPO/logs/msb-v3.log"
SCRIPT="$REPO/scripts/run.sh"

mkdir -p "$REPO/.artifacts" "$REPO/logs"

if [ -f "$PIDFILE" ] && ps -p "$(cat "$PIDFILE")" >/dev/null 2>&1; then
  echo "msb-v3 already running pid=$(cat "$PIDFILE")"
  exit 1
fi

nohup bash "$SCRIPT" > "$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"
echo "started msb-v3 pid=$(cat "$PIDFILE") log=$LOGFILE"
