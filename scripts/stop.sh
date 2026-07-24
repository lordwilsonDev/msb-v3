#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/lordwilson/msb-v3"
PIDFILE="$REPO/.artifacts/msb-v3.pid"

if [ -f "$PIDFILE" ]; then
  pid=$(cat "$PIDFILE")
  if ps -p "$pid" >/dev/null 2>&1; then
    kill "$pid" && echo "stopped msb-v3 pid=$pid" || echo "failed to stop pid=$pid"
    rm -f "$PIDFILE"
    exit 0
  fi
  rm -f "$PIDFILE"
fi
echo "msb-v3 not running"
