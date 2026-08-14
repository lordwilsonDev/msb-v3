#!/usr/bin/env bash
set -euo pipefail

# webcheck -- one-command BROWSER smoke test of the msb-v3 live server.
#
# Renders each lifecycle endpoint in a REAL browser (system Chrome driven by
# Playwright via ~/bin/webcheck.py -- no ego-browser, no headless hacks):
#   GET /status      health: service/version/ready/model
#   GET /mcp/status  MCP bridge: service/version/ready/tools (auth-gated)
#   GET /metrics     redirects to /metrics/: ready + prometheus path
#
# For each endpoint: page is loaded, visible text + screenshot written under
# artifacts/webcheck-<ts>/, and console errors / failed requests captured.
# Exit code is non-zero if any endpoint fails to load or reports real page
# errors (favicon noise filtered by webcheck.py).
#
# Usage:
#   make webcheck                  # default host/port (127.0.0.1:8766)
#   MSB_PORT=8767 make webcheck    # point at another instance

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
PY="/opt/homebrew/Caskroom/miniforge/base/bin/python"
WEBCHECK="$HOME/bin/webcheck.py"
HOST="${MSB_HOST:-127.0.0.1}"
PORT="${MSB_PORT:-8766}"
BASE="http://${HOST}:${PORT}"

# Multi-step UI flows: set FLOW to a check-script (relative to REPO or
# absolute) to run a click/fill/assert flow instead of the 3 endpoint checks:
#   make webcheck FLOW=scripts/webcheck/n8n-signin.json
FLOW="${FLOW:-}"
if [ -n "$FLOW" ]; then
  case "$FLOW" in
    /*) FLOW_PATH="$FLOW" ;;
    *)  FLOW_PATH="$REPO/$FLOW" ;;
  esac
  OUT="$REPO/artifacts/webcheck-flow-$(date +%Y%m%dT%H%M%SZ)"
  mkdir -p "$OUT"
  if "$PY" "$WEBCHECK" run "$FLOW_PATH" --out "$OUT"; then
    echo "[webcheck] flow PASSED: $FLOW (artifacts: $OUT)"
    exit 0
  fi
  echo "[webcheck] flow FAILED: $FLOW (artifacts: $OUT)" >&2
  exit 1
fi

# /mcp/status is auth-gated by x-mcp-secret. Resolve the secret the same way
# the running server does: env override, then .env, then the shipped default
# (matches scripts/run.sh). Unset secret = server in dev mode (no gate).
SECRET="${MCP_BRIDGE_SECRET:-}"
if [ -z "$SECRET" ] && [ -f "$REPO/.env" ]; then
  SECRET=$(sed -nE 's/^MCP_BRIDGE_SECRET="?([^"]*)"?/\1/p' "$REPO/.env" | head -1)
fi
if [ -z "$SECRET" ]; then
  echo "[webcheck] WARNING: MCP_BRIDGE_SECRET not found in env or $REPO/.env; /mcp/status will 401 unless the server runs in dev mode" >&2
fi

OUT="$REPO/artifacts/webcheck-$(date +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"

if ! curl -fsS -m 3 "$BASE/status" >/dev/null 2>&1; then
  echo "[webcheck] ERROR: msb-v3 not reachable at $BASE -- start it first (make server-start)" >&2
  exit 1
fi

pass=0
fail=0

check() {
  local name="$1" url="$2"
  shift 2
  if "$PY" "$WEBCHECK" check "$url" "$@" --shot "$OUT/$name.png" >"$OUT/$name.log" 2>&1; then
    echo "ok   $name"
    pass=$((pass + 1))
  else
    echo "FAIL $name"
    fail=$((fail + 1))
  fi
  sed -E 's/^(BODY|CONSOLE|SHOT|FAILED|LOAD)/     \1/' "$OUT/$name.log" | head -6
}

check status "$BASE/status"
check mcp_status "$BASE/mcp/status" --header "x-mcp-secret: $SECRET"
check metrics "$BASE/metrics"

echo "passed=$pass failed=$fail (artifacts: $OUT)"
[ "$fail" -eq 0 ]
