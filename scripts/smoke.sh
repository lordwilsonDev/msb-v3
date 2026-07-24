#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/lordwilson/msb-v3"
PY="/opt/homebrew/Caskroom/miniforge/base/bin/python"
HOST="${MSB_HOST:-127.0.0.1}"
PORT="${MSB_PORT:-8767}"
BASE="http://${HOST}:${PORT}"

pass=0
fail=0

check() {
  local name="$1"
  local url="$2"
  if curl -fsS "$url" >/dev/null 2>&1; then
    echo "ok $name"
    pass=$((pass + 1))
  else
    echo "FAIL $name"
    fail=$((fail + 1))
  fi
}

if curl -fsS "$BASE/health" >/dev/null; then
  check health "$BASE/health"
  check routes "$BASE/system/routes"
  check metrics "$BASE/metrics/"
  check prometheus "$BASE/metrics/prometheus"
else
  echo "server not reachable at $BASE"
  exit 1
fi

echo "passed=$pass failed=$fail"
if [ "$fail" -ne 0 ]; then exit 1; fi
