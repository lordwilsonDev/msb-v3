#!/usr/bin/env bash
# factory_gate_daily.sh — run the engineering-hygiene factory gate, alert if it stops being PASS.
#
# Designed for launchd (StartCalendarInterval, daily). The factory needs the
# live msb-v3 server (the auth probe + most experiments require it), so this
# script ensures the server is up first (idempotently via tmux), then runs
# the gate, then ALERTS if the verdict is anything other than PASS.
# Automatically commits PASS evidence (pushed) or FAIL evidence (local only),
# and records events in artifacts/hygiene/daily_gate_events.jsonl.
#
# It also records daily identity-shadow runtime evidence before the factory runs
# (K22 criterion 1's only source of evidence — a test run can only ever write
# "test" records). That step is EVIDENCE, NOT VERDICT: it never affects the
# PASS/FAIL decision below. See scripts/identity_shadow_daily.py for why.
#
#   run manually:  bash scripts/factory_gate_daily.sh
#   log:           $HOME/Library/Logs/msb-factory-gate.log
#
# Exit codes (as actually implemented, corrected 2026-10-11 — the old header
# claimed 0 for BLOCKED-with-no-fail, which the code never did):
#   0 = gate PASS
#   1 = gate not PASS (FAILED or BLOCKED; alert sent) | self-test failure |
#       factory crashed (non-zero exit with no readable verdict)
#   2 = could not start the server
set -uo pipefail

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
# launchd jobs have no WorkingDirectory (cwd=/) — the factory and the member
# runners use repo-relative paths (e.g. the app's data/ dir), so anchor here.
cd "$REPO" || exit 1
FACTORY="${FACTORY_RUNNER:-$HOME/.hermes/skills/engineering/engineering-hygiene-factory/scripts/run_factory.py}"
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"
# Overridable like MSB_REPO / MSB_PYTHON / FACTORY_RUNNER above: the start path
# is the part that decides whether a second supervisor gets spawned, so a test
# must be able to drive it against a stub service and a scratch log instead of
# the live :8766 and the real log file.
LOG="${MSB_GATE_LOG:-$HOME/Library/Logs/msb-factory-gate.log}"
BASE_URL="${MSB_BASE_URL:-http://127.0.0.1:8766}"
SESSION="msb-v3"
EVENTS_LOG="$REPO/artifacts/hygiene/daily_gate_events.jsonl"

mkdir -p "$(dirname "$LOG")"
mkdir -p "$REPO/artifacts/hygiene"

log() { echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] $*" | tee -a "$LOG"; }

send_notification() {
  local msg="$1"
  local verdict="${2:-UNKNOWN}"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  
  if osascript -e "display notification \"$msg\" with title \"msb-factory-gate\" sound name \"Sosumi\"" 2>/dev/null; then
    log "notification sent: $msg"
  else
    log "notification failed: $msg"
    printf '{"ts": "%s", "event": "notification_failure", "verdict": "%s", "message": "%s"}\n' "$ts" "$verdict" "$msg" >> "$EVENTS_LOG"
  fi
}

# 1. Ensure the server is up.
#
# Two probes, not one. A restart-cycling service answers a single 2s curl
# whenever the probe happens to land in an up-phase, so "already up" off one
# sample reports healthy for a service that is half-down -- exactly what
# happened through the 2026-10-10/11 incident, when the gate logged "server
# already up" while the service was restarting every ~14s.
server_up() {
  curl -s -m 2 "$BASE_URL/health" >/dev/null 2>&1 || return 1
  sleep 1
  curl -s -m 2 "$BASE_URL/health" >/dev/null 2>&1
}

MSB_LABEL="com.lordwilson.msb-v3"

if server_up; then
  log "server already up (2/2 probes)"
else
  # Prefer the launchd supervisor: kickstart restarts that one process, which
  # is the service's real owner. Spawning a *second* supervisor under tmux
  # (what this script did before 2026-10-11) is what created the restart war --
  # two loops both call reclaim_stale_port(), which kills any port-holder that
  # looks like msb_v3 as if it were an unsupervised orphan, so each killed the
  # other's live child every ~14s for 29h (7268 restarts). Never start a rival
  # supervisor while launchd has the job loaded.
  if launchctl print "gui/$(id -u)/$MSB_LABEL" >/dev/null 2>&1; then
    log "server down -- kickstarting launchd supervisor ($MSB_LABEL)"
    launchctl kickstart -k "gui/$(id -u)/$MSB_LABEL" 2>>"$LOG" \
      || log "WARN: launchctl kickstart returned non-zero"
  else
    log "server down and $MSB_LABEL not loaded -- starting under tmux ($SESSION)"
    log "WARN: that supervisor is not launchd-managed; ensure no second one is running"
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      tmux kill-session -t "$SESSION" 2>/dev/null || true
    fi
    tmux new-session -d -s "$SESSION" "cd $REPO && bash scripts/run.sh"
  fi
  for _ in $(seq 1 30); do
    if server_up; then
      log "server up after start"
      break
    fi
    sleep 2
  done
  if ! server_up; then
    log "ERROR: server failed to come up -- gate not run; alerting"
    send_notification "msb-v3 server did not start; factory gate NOT run" "SERVER_FAIL"
    exit 2
  fi
fi

# 2. Canary: prove the zero-spend env scrub holds BEFORE spending a full
# gate run.
if ! "$PY" "$FACTORY" --self-test > /tmp/factory_gate_self_test.log 2>>"$LOG"; then
  log "ERROR: zero-spend self-test FAILED — scrub broken; gate not run"
  send_notification "Factory zero-spend self-test failed — see log" "SELF_TEST_FAIL"
  exit 1
fi
log "zero-spend self-test OK"

# 3. Identity-shadow runtime evidence — the heartbeat K22 criterion 1 needs.
#
# Criterion 1 is judged on RUNTIME-origin records, and a test run can only ever
# write "test" ones, so without a live caller the criterion has no source of
# evidence and reads INSUFFICIENT DATA (its state until 2026-09-20). This step is
# that caller, and it runs BEFORE the factory so evidence accrues even when the
# gate later fails.
#
# EVIDENCE, NOT VERDICT: the exit code below is logged as a warning and never
# reaches the PASS/FAIL decision. Two measured reasons — the /chat half depends
# on an 8B model choosing to call a tool, so a miss is not a regression, and the
# bridge half performs a REAL vault write (99_Meta/identity-shadow-probe.md),
# which a gate must not escalate on. What it does do is stop the evidence rotting
# silently: every run is recorded in the hygiene evidence this script commits and
# pushes, and the step asks for a notification when a surface misses twice in a
# row — the point at which "the model didn't feel like it" stops explaining it.
IDENTITY_STEP="$REPO/scripts/identity_shadow_daily.py"
IDENTITY_ALERT="$(mktemp -t identity-shadow-alert)"
if [ -f "$IDENTITY_STEP" ]; then
  log "identity-shadow: recording runtime evidence..."
  identity_report=$(PYTHONPATH="$REPO/src" "$PY" "$IDENTITY_STEP" --alert-file "$IDENTITY_ALERT" 2>>"$LOG")
  identity_rc=$?
  while IFS= read -r line; do
    if [ -n "$line" ]; then log "$line"; fi
  done <<< "$identity_report"
  if [ "$identity_rc" -ne 0 ]; then
    log "WARN: identity-shadow evidence step exited $identity_rc — gate verdict unaffected"
  fi
  if [ -s "$IDENTITY_ALERT" ]; then
    send_notification "$(cat "$IDENTITY_ALERT")" "IDENTITY_SHADOW"
  fi
else
  log "identity-shadow: $IDENTITY_STEP missing — evidence step skipped"
fi
rm -f "$IDENTITY_ALERT"

# 4. Run the factory gate.
log "running factory gate..."
# run_factory's documented contract (its own header, line 32): exit **0** when
# the gate is PASS or BLOCKED-with-no-fail, and **exit 1 on any FAIL**. Exit 1
# is therefore a VERDICT, not a crash. Until 2026-10-11 this script treated any
# non-zero exit as a crash and returned *before* reading the verdict, which did
# two things silently: it skipped the gate_run line in daily_gate_events.jsonl
# (the record stopped dead on the first FAILED day — last entry 2026-10-09),
# and it skipped the FAIL-evidence commit below, so a failing gate left no
# committed trace at all and reported a misleading "crashed" notification.
# The crash branch is now "exited non-zero AND wrote no readable verdict".
MSB_REPO="$REPO" "$PY" "$FACTORY" > /tmp/factory_gate_daily_run.json 2>>"$LOG"
rc=$?

VERDICT=$(REPO="$REPO" python3 -c '
import json, os
try:
    d = json.load(open(os.path.join(os.environ["REPO"], "artifacts/hygiene/factory_gate.json")))
    print(d["RELEASE_VERDICT"]["release_verdict"])
except Exception:
    print("UNKNOWN")
' 2>/dev/null)

UNKNOWNS=$(REPO="$REPO" python3 -c '
import json, os
try:
    d = json.load(open(os.path.join(os.environ["REPO"], "artifacts/hygiene/factory_gate.json")))
    print(len(d["RELEASE_VERDICT"].get("unresolved_unknowns", [])))
except Exception:
    print(-1)
' 2>/dev/null)

# Distinguish a real crash from a FAIL verdict. VERDICT is already extracted
# above; only an unreadable verdict plus a non-zero exit is a crash.
if [ "$VERDICT" = "UNKNOWN" ] && [ "$rc" -ne 0 ]; then
  log "ERROR: factory exited $rc and wrote no readable verdict — treating as a crash; see /tmp/factory_gate_daily_run.json"
  send_notification "Factory gate crashed (exit $rc, no verdict written) — see log" "CRASH"
  exit 1
fi
if [ "$rc" -ne 0 ]; then
  log "factory exited $rc (= FAIL per run_factory's documented contract)"
fi

log "gate verdict=$VERDICT unknowns=$UNKNOWNS"

ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"ts": "%s", "event": "gate_run", "verdict": "%s", "unknowns": %s}\n' "$ts" "$VERDICT" "$UNKNOWNS" >> "$EVENTS_LOG"

# 5. Self-committing evidence logic
if git -C "$REPO" status --porcelain -- artifacts/hygiene/ | grep -q .; then
  if [ "$VERDICT" = "PASS" ]; then
    git -C "$REPO" add artifacts/hygiene/
    git -C "$REPO" commit -m "chore: daily gate evidence (PASS) $(date +%Y-%m-%d)"
    # This push carries only artifacts/hygiene/ evidence, never source — the
    # repo's pre-push hook (lint/mypy/policy + a full pytest rerun in a staged
    # copy, ~5min) gates the whole working tree regardless of what's being
    # pushed, and reliably blew the 60s bound below long before portability
    # even started. That was the actual cause of every prior "push FAILED"
    # (root-caused 2026-09-16; not a launchd/keychain issue as originally
    # guessed). MSB_SKIP_PORTABILITY=1 short-circuits the entire hook (see
    # scripts/hooks/pre-push), which is fine here since this commit never
    # touches src/ or tests/ — there's nothing for lint/mypy to catch. Bound
    # the push regardless so a genuine auth/network hang still can't stall
    # the job or leave stale committed evidence unobserved. macOS has no GNU
    # `timeout`, so fall back to a perl alarm wrapper (same 60s bound) when
    # it is absent. Outcome is recorded in the events log either way.
    if command -v timeout >/dev/null 2>&1; then
      PUSH_CMD=(env MSB_SKIP_PORTABILITY=1 timeout 60 git -C "$REPO" push origin HEAD)
    else
      PUSH_CMD=(env MSB_SKIP_PORTABILITY=1 perl -e 'alarm shift; exec @ARGV' 60 git -C "$REPO" push origin HEAD)
    fi
    if "${PUSH_CMD[@]}" >/dev/null 2>&1; then
      log "committed and pushed PASS evidence"
      printf '{"ts": "%s", "event": "evidence_push", "ok": true, "verdict": "%s"}\n' "$ts" "$VERDICT" >> "$EVENTS_LOG"
    else
      log "WARN: evidence committed locally but push FAILED — will push on next run"
      printf '{"ts": "%s", "event": "evidence_push", "ok": false, "verdict": "%s"}\n' "$ts" "$VERDICT" >> "$EVENTS_LOG"
    fi
  else
    git -C "$REPO" add artifacts/hygiene/
    git -C "$REPO" commit -m "chore: daily gate evidence ($VERDICT) $(date +%Y-%m-%d) [do-not-push]"
    log "committed $VERDICT evidence locally (unpushed)"
  fi
fi

# 6. Alert on anything that is not a clean PASS.
if [ "$VERDICT" != "PASS" ]; then
  log "ALERT: gate is $VERDICT (not PASS) — investigating required"
  send_notification "Factory gate is $VERDICT ($UNKNOWNS unknowns) — not PASS" "$VERDICT"
  exit 1
fi

log "gate PASS — all evidence green"
exit 0
