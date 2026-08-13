#!/usr/bin/env bash
# freshen-harness-evidence.sh — keep the harness-gate evidence inside its
# freshness window without human action.
#
# harness-gate (msb-v3 CI, self-hosted runner) BLOCKS when the newest
# video-harness evidence for p0_basic / p1_ffmpeg / p2_inference is older
# than HARNESS_MAX_AGE_H (default 24h) or its verdict is not PASS. This daily
# job makes that a non-event:
#
#   1. Judges freshness by running the repo's own harness-evidence.sh gate
#      with HARNESS_MAX_AGE_H=FRESH_THRESHOLD_H (default 12h — half the
#      gate's 24h window, so a missed day still fits) — the skip-guard can
#      never drift from what CI will judge.
#   2. If every experiment is fresh: log + skip (no redundant runs).
#   3. Otherwise re-run the three baseline experiments (make run / run-p1 /
#      run-p2 — deterministic synthetic runs, seconds each) and fail loudly
#      on any non-PASS verdict.
#   4. Proves the result with the same gate (report written), so the log
#      ends with the exact verdict CI will see.
#   5. On any failure, sends a macOS notification banner (osascript).
#
# Designed for launchd StartCalendarInterval (see
# com.blackswanlabz.harness-evidence.plist). Also safe to run manually:
#   bash scripts/freshen-harness-evidence.sh
# Log: $HOME/Library/Logs/msb-harness-evidence.log
#
# Env:
#   HARNESS_DIR          video-harness root (default ~/video-harness)
#   HARNESS_EXPERIMENTS  comma-separated experiment ids (default
#                        p0_basic,p1_ffmpeg,p2_inference — must match the
#                        make run/run-p1/run-p2 mapping below)
#   FRESH_THRESHOLD_H    freshness skip-guard in hours (default 12)
#   MSB_REPO             msb-v3 repo root for the gate script
#                        (default ~/msb-v3)
#
# Exit: 0 = evidence fresh (skipped or refreshed + gate PASS)
#       1 = refresh attempted but an experiment or the final gate FAILED
#       2 = env/config error
set -uo pipefail

HARNESS_DIR="${HARNESS_DIR:-$HOME/video-harness}"
EVIDENCE_DIR="$HARNESS_DIR/evidence"
EXPERIMENTS="${HARNESS_EXPERIMENTS:-p0_basic,p1_ffmpeg,p2_inference}"
THRESHOLD_H="${FRESH_THRESHOLD_H:-12}"
REPO="${MSB_REPO:-$HOME/msb-v3}"
GATE_SCRIPT="$REPO/scripts/harness-evidence.sh"
LOG="$HOME/Library/Logs/msb-harness-evidence.log"

mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] $*" | tee -a "$LOG"; }

# Fail fast on environment problems BEFORE touching anything. The gate is
# invoked via `bash script.sh` (repo convention — harness-evidence.sh is
# mode 644, not +x), so check existence, not the exec bit.
[ -d "$EVIDENCE_DIR" ] || { log "ERROR: evidence dir not found: $EVIDENCE_DIR (set HARNESS_DIR)"; exit 2; }
[ -f "$GATE_SCRIPT" ]  || { log "ERROR: gate script not found: $GATE_SCRIPT (set MSB_REPO)"; exit 2; }

send_notification() {
  local msg="$1"
  if osascript -e "display notification \"$msg\" with title \"harness-evidence\" sound name \"Sosumi\"" 2>/dev/null; then
    log "notification sent: $msg"
  else
    log "notification failed: $msg"
  fi
}

# --- 1. Judge freshness with the gate itself (single source of truth) ---
# The skip-guard IS harness-evidence.sh with HARNESS_MAX_AGE_H=THRESHOLD_H,
# so the freshener can never drift from what CI will judge: rc 0 = every
# experiment's newest run is PASS and under the guard age (skip);
# rc 1 = missing / stale / non-PASS (refresh); rc 2 = env/config error
# (stop, touch nothing). The gate reads HARNESS_EXPERIMENTS itself, so
# overrides flow through. The whole output block is captured for the log
# (the gate routes it to stderr on failure).
judgment=$(HARNESS_MAX_AGE_H="$THRESHOLD_H" HARNESS_SUMMARY=0 bash "$GATE_SCRIPT" 2>&1)
rc=$?
if [ "$rc" -eq 2 ]; then
  log "ERROR: freshness judgment errored (rc=2):"
  echo "$judgment" | tee -a "$LOG" >&2
  send_notification "harness-evidence freshness check errored — see $LOG"
  exit 2
fi
echo "$judgment" | tee -a "$LOG"
if [ "$rc" -eq 0 ]; then
  log "all experiments fresh — skipping refresh"
  exit 0
fi

# --- 2. Refresh: re-run the three baseline experiments (deterministic, fast) ---
log "refreshing evidence ($EXPERIMENTS)..."
cd "$HARNESS_DIR" || { log "ERROR: cannot cd $HARNESS_DIR"; send_notification "harness-evidence: cannot cd $HARNESS_DIR"; exit 2; }
fail=0
for target in run run-p1 run-p2; do
  if make "$target" > "/tmp/harness-freshen-$target.log" 2>&1; then
    log "make $target: PASS"
  else
    log "make $target: FAILED — tail:"
    tail -5 "/tmp/harness-freshen-$target.log" | tee -a "$LOG"
    fail=1
  fi
done
if [ "$fail" -ne 0 ]; then
  send_notification "video-harness experiment failed — harness-gate will block on evidence"
  exit 1
fi

# --- 3. Prove it with the repo's own gate (report written) ---
log "verifying with harness-evidence.sh..."
if HARNESS_REPORT_FILE=/tmp/harness-evidence-freshen-report.json bash "$GATE_SCRIPT" > /tmp/harness-freshen-gate.log 2>&1; then
  tail -4 /tmp/harness-freshen-gate.log | tee -a "$LOG"
  log "gate PASS — evidence fresh"
  exit 0
else
  tail -6 /tmp/harness-freshen-gate.log | tee -a "$LOG" >&2
  log "gate FAIL after refresh — harness-gate will block"
  send_notification "harness-evidence refresh did not restore gate PASS — see $LOG"
  exit 1
fi
