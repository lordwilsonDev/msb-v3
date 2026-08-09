#!/usr/bin/env bash
#
# install_factory_gate.sh — install/validate the daily factory-gate LaunchAgent.
#
#   ./scripts/install_factory_gate.sh
#
# Idempotent: copies the versioned plist to ~/Library/LaunchAgents, (re)loads
# it, and validates the job is registered. It does NOT run the full gate (that
# takes minutes); use `bash scripts/factory_gate_daily.sh` to run it manually.
#
# Exit codes: 0 installed | 1 failure
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$ROOT/scripts/com.blackswanlabz.msb-factory-gate.plist"
AGENT="$HOME/Library/LaunchAgents/com.blackswanlabz.msb-factory-gate.plist"
LABEL="com.blackswanlabz.msb-factory-gate"
UID_NUM="$(id -u)"

if [ ! -f "$PLIST" ]; then
  echo "FAIL: $PLIST not found" >&2
  exit 1
fi

cp "$PLIST" "$AGENT"
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$AGENT"

if launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1; then
  echo "OK: daily factory gate installed (runs daily 06:15)"
  echo "    test manually: bash $ROOT/scripts/factory_gate_daily.sh"
  exit 0
fi

echo "FAIL: launchd job not registered — see /tmp/msb-factory-gate-launchd.log" >&2
exit 1
