#!/usr/bin/env bash
# factory_gate_daily.sh — run the engineering-hygiene factory gate, alert if it stops being PASS.
#
# Designed for launchd (StartCalendarInterval, daily). The factory needs the
# live msb-v3 server (the auth probe + most experiments require it), so this
# script ensures the server is up first (idempotently via tmux), then runs
# the gate, then ALERTS if the verdict is anything other than PASS.
#
#   run manually:  bash scripts/factory_gate_daily.sh
#   log:           $HOME/Library/Logs/msb-factory-gate.log
#
# Exit codes: 0 = gate PASS or BLOCKED-with-no-fail (server started/up, gate ran)
#             1 = gate FAILED (alert sent) | 2 = could not start the server
set -uo pipefail

REPO="/Users/lordwilson/msb-v3"
FACTORY="/Users/lordwilson/.hermes/skills/engineering/engineering-hygiene-factory/scripts/run_factory.py"
PY="/opt/homebrew/Caskroom/miniforge/base/bin/python"
LOG="$HOME/Library/Logs/msb-factory-gate.log"
BASE_URL="http://127.0.0.1:8766"
SESSION="msb-v3"

mkdir -p "$(dirname "$LOG")"
log() { echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] $*" | tee -a "$LOG"; }

# 1. Ensure the server is up (idempotent; start under tmux if not listening).
if curl -s -m 2 "$BASE_URL/health" >/dev/null 2>&1; then
  log "server already up"
else
  log "server down — starting under tmux ($SESSION)"
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION" 2>/dev/null || true
  fi
  tmux new-session -d -s "$SESSION" "cd $REPO && bash scripts/run.sh"
  for _ in $(seq 1 30); do
    if curl -s -m 2 "$BASE_URL/health" >/dev/null 2>&1; then
      log "server up after start"
      break
    fi
    sleep 2
  done
  if ! curl -s -m 2 "$BASE_URL/health" >/dev/null 2>&1; then
    log "ERROR: server failed to come up — gate not run; alerting"
    osascript -e 'display notification "msb-v3 server did not start; factory gate NOT run" with title "msb-factory-gate" sound name "Sosumi"' 2>/dev/null || true
    exit 2
  fi
fi

# 2. Canary: prove the zero-spend env scrub holds BEFORE spending a full
# gate run. The self-test injects sentinel credentials and asserts no
# subprocess the gate spawns can see them (via the shared _spawn choke point).
if ! "$PY" "$FACTORY" --self-test > /tmp/factory_gate_self_test.log 2>>"$LOG"; then
  log "ERROR: zero-spend self-test FAILED — scrub broken; gate not run"
  osascript -e 'display notification "Factory zero-spend self-test failed — see log" with title "msb-factory-gate" sound name "Sosumi"' 2>/dev/null || true
  exit 1
fi
log "zero-spend self-test OK"

# 3. Run the factory gate. Capture the real exit code ($? inside an `if !`
# branch would be the status of the negation, not the factory's).
log "running factory gate..."
MSB_REPO="$REPO" "$PY" "$FACTORY" > /tmp/factory_gate_daily_run.json 2>>"$LOG"
rc=$?
if [ "$rc" -ne 0 ]; then
  log "ERROR: factory crashed (exit $rc) — see /tmp/factory_gate_daily_run.json"
  osascript -e 'display notification "Factory gate crashed — see log" with title "msb-factory-gate" sound name "Sosumi"' 2>/dev/null || true
  exit 1
fi

VERDICT=$(python3 -c '
import json, sys
try:
    d = json.load(open("/Users/lordwilson/msb-v3/artifacts/hygiene/factory_gate.json"))
    print(d["RELEASE_VERDICT"]["release_verdict"])
except Exception as e:
    print("UNKNOWN")
' 2>/dev/null)
UNKNOWNS=$(python3 -c '
import json
try:
    d = json.load(open("/Users/lordwilson/msb-v3/artifacts/hygiene/factory_gate.json"))
    print(len(d["RELEASE_VERDICT"].get("unresolved_unknowns", [])))
except Exception:
    print(-1)
' 2>/dev/null)

log "gate verdict=$VERDICT unknowns=$UNKNOWNS"

# 4. Alert on anything that is not a clean PASS.
if [ "$VERDICT" != "PASS" ]; then
  log "ALERT: gate is $VERDICT (not PASS) — investigating required"
  osascript -e "display notification \"Factory gate is $VERDICT ($UNKNOWNS unknowns) — not PASS\" with title \"msb-factory-gate\" sound name \"Sosumi\"" 2>/dev/null || true
  exit 1
fi

log "gate PASS — all evidence green"
exit 0
