#!/usr/bin/env bash
set -uo pipefail

# webcheck-all -- the full browser verification suite in one command.
#
# Runs browser checks as a sequence of stages. Which stages run, and in what
# order, is controlled by STAGES (comma-separated, any subset, any order):
#   endpoints  live server endpoints: /status, /mcp/status, /metrics
#   desktop    client-facing HTML deliverables on the Desktop
#   flow       one UI flow (default: authed /mcp/status assertions via
#              scripts/webcheck/msb-status.json; override with FLOW=<script>)
#   custom     an arbitrary command given in CUSTOM_CMD
#   harness    video-harness evidence producer + CI-consumer gate in one
#              stage (scripts/harness-evidence.sh writes the v1 report,
#              then scripts/ci-harness-gate.sh gates it)
#
# Every requested stage runs even if an earlier one failed, so you get the
# full picture in one pass; the exit code is non-zero if ANY stage failed.
# `endpoints` and `flow` need the server running (make server-start);
# `desktop` does not. Logs/artifacts land under artifacts/ (flow gets its
# own timestamped subdir; the others log directly into artifacts/).
#
# Usage:
#   make webcheck-all                        # STAGES=endpoints,desktop,flow
#   make webcheck-all STAGES=flow            # just the flow
#   make webcheck-all STAGES=endpoints,custom CUSTOM_CMD="bash scripts/foo.sh"
#   make webcheck-all STAGES=endpoints,harness   # evidence -> gate, one command
#   make webcheck-all FLOW=scripts/webcheck/n8n-signin.json

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
PY="/opt/homebrew/Caskroom/miniforge/base/bin/python"
WEBCHECK="$HOME/bin/webcheck.py"
HOST="${MSB_HOST:-127.0.0.1}"
PORT="${MSB_PORT:-8766}"
BASE="http://${HOST}:${PORT}"

# Stage logs are redirected to $REPO/artifacts/ by the PARENT shell before
# the stage commands run (which would create the dir only after), so create
# it up front or a fresh checkout fails the redirect itself.
mkdir -p "$REPO/artifacts"

# /mcp/status is auth-gated by x-mcp-secret. Resolve the secret the same way
# webcheck.sh / the server do (env -> .env -> shipped default) and export it
# so flow scripts can interpolate ${MCP_BRIDGE_SECRET}.
export MCP_BRIDGE_SECRET="${MCP_BRIDGE_SECRET:-}"
if [ -z "$MCP_BRIDGE_SECRET" ] && [ -f "$REPO/.env" ]; then
  MCP_BRIDGE_SECRET=$(sed -nE 's/^MCP_BRIDGE_SECRET="?([^"]*)"?/\1/p' "$REPO/.env" | head -1)
fi
if [ -z "$MCP_BRIDGE_SECRET" ]; then
  echo "[webcheck-all] WARNING: MCP_BRIDGE_SECRET not found in env or $REPO/.env; /mcp/status will 401 unless the server runs in dev mode" >&2
fi

fail=0

stage() {
  local name="$1" log="$2"
  shift 2
  echo
  echo "===== [$name] ====="
  if "$@" >"$log" 2>&1; then
    echo "[$name] PASSED (log: $log)"
    if [ -s "$log" ]; then
      # one-line summary on pass (e.g. "passed=3 failed=0")
      tail -1 "$log" | sed 's/^/    /'
    fi
  else
    echo "[$name] FAILED (log: $log)"
    fail=$((fail + 1))
    # show the tail so failures are visible without opening the log
    if [ -s "$log" ]; then
      echo "  --- tail ---"
      tail -8 "$log" | sed 's/^/    /'
    fi
  fi
}

# -- stage functions ---------------------------------------------------------

# endpoints: live server checks. FLOW= is explicitly cleared here: when this
# wrapper is invoked as `make webcheck-all FLOW=...`, make exports FLOW to
# the recipe env and webcheck.sh would otherwise run the flow instead of the
# endpoint checks.
stage_endpoints() {
  stage "endpoints (server)" \
    "$REPO/artifacts/webcheck-all-endpoints.log" \
    env FLOW= bash "$REPO/scripts/webcheck.sh"
}

# desktop: client-facing HTML deliverables (no server needed).
stage_desktop() {
  stage "desktop (deliverables)" \
    "$REPO/artifacts/webcheck-all-desktop.log" \
    bash "$REPO/scripts/webcheck-desktop.sh"
}

# flow: one UI check-script. Default is the authed /mcp/status flow.
stage_flow() {
  local flow="${FLOW:-scripts/webcheck/msb-status.json}" flow_path flow_out
  case "$flow" in
    /*) flow_path="$flow" ;;
    *)  flow_path="$REPO/$flow" ;;
  esac
  if [ ! -f "$flow_path" ]; then
    echo "[flow] ERROR: flow script not found: $flow_path" >&2
    fail=$((fail + 1))
    return
  fi
  flow_out="$REPO/artifacts/webcheck-flow-$(date +%Y%m%dT%H%M%SZ)"
  mkdir -p "$flow_out"
  stage "flow ($flow)" \
    "$flow_out/flow.log" \
    "$PY" "$WEBCHECK" run "$flow_path" --out "$flow_out" --base "$BASE"
}

# custom: an arbitrary command (e.g. harness evidence check, an n8n flow run,
# or any one-off verification) supplied via CUSTOM_CMD.
stage_custom() {
  if [ -z "${CUSTOM_CMD:-}" ]; then
    echo "[custom] ERROR: CUSTOM_CMD is empty -- set it, e.g. CUSTOM_CMD='bash scripts/harness-evidence.sh'" >&2
    fail=$((fail + 1))
    return
  fi
  stage "custom ($CUSTOM_CMD)" \
    "$REPO/artifacts/webcheck-all-custom.log" \
    bash -c "$CUSTOM_CMD"
}

# harness: video-harness evidence producer -> CI-consumer gate in ONE stage.
# scripts/harness-evidence.sh writes the harness-evidence-report/v1 JSON
# artifact, then scripts/ci-harness-gate.sh gates it -- so the routine
# exercises the whole producer/consumer chain. The report path comes from
# HARNESS_REPORT_FILE (default $REPO/artifacts/harness-evidence-report.json)
# and is forced for the producer even when the env var is unset; all other
# HARNESS_* envs pass through (e.g. HARNESS_EXPERIMENTS, HARNESS_STRICT).
# Both commands run even if the producer fails -- the report is still written
# on a FAIL, and the consumer's BLOCK detail lands in the same log -- and the
# stage fails if either fails.
stage_harness() {
  local report="${HARNESS_REPORT_FILE:-$REPO/artifacts/harness-evidence-report.json}"
  stage "harness (evidence -> gate)" \
    "$REPO/artifacts/webcheck-all-harness.log" \
    bash -c "HARNESS_REPORT_FILE='$report' bash '$REPO/scripts/harness-evidence.sh'; p=\$?; bash '$REPO/scripts/ci-harness-gate.sh' '$report'; c=\$?; [ \$p -eq 0 ] && [ \$c -eq 0 ]"
}

# -- dispatch ----------------------------------------------------------------

# Parse STAGES into an ordered list, dropping empty/whitespace-only entries
# (a trailing or doubled comma would otherwise yield an empty stage name
# that "matches nothing" and silently skips). Unset STAGES (or a value that
# yields no usable entries) means the default full run. Unknown names are
# loud errors, not skips.
STAGES="${STAGES:-endpoints,desktop,flow}"
stage_list=()
IFS=',' read -r -a _raw_stages <<< "$STAGES"
for s in "${_raw_stages[@]}"; do
  s="${s//[[:space:]]/}"   # tolerate 'flow, desktop'
  [ -n "$s" ] && stage_list+=("$s")
done
if [ "${#stage_list[@]}" -eq 0 ]; then
  echo "[webcheck-all] ERROR: no usable stages selected from STAGES='$STAGES' " \
       "(want e.g. endpoints,desktop,flow,custom,harness)" >&2
  exit 1
fi

for s in "${stage_list[@]}"; do
  case "$s" in
    endpoints) stage_endpoints ;;
    desktop)   stage_desktop ;;
    flow)      stage_flow ;;
    custom)    stage_custom ;;
    harness)   stage_harness ;;
    *)
      echo "[webcheck-all] ERROR: unknown stage '$s' (known: endpoints, desktop, flow, custom, harness)" >&2
      fail=$((fail + 1))
      ;;
  esac
done

echo
if [ "$fail" -eq 0 ]; then
  echo "webcheck-all: ALL STAGES PASSED"
  exit 0
fi
echo "webcheck-all: $fail stage(s) FAILED" >&2
exit 1
