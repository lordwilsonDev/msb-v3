#!/usr/bin/env bash
set -uo pipefail

# harness-gate-dryrun -- local pre-push dry-run of the self-hosted CI gate.
#
# Mirrors .github/workflows/harness-gate.yml step for step so a red CI gate
# never surprises you:
#   1. ensure Qdrant is up   (best-effort `make qdrant-start` + probe :6333)
#   2. ensure msb-v3 is up   (best-effort `make server-start` + probe :8766)
#   3. make webcheck-all STAGES=endpoints,harness   <- the gate itself
#
# Differences from the workflow: no artifact upload (CI-only), MSB_REPO stays
# the local checkout (defaults to the repo root, not a fresh workspace copy),
# and every step runs even if an earlier one failed so you see the full
# picture -- the exit code is non-zero if ANY step failed.
#
# Usage:
#   make harness-gate-dryrun
#   bash scripts/harness-gate-dryrun.sh
#
# Env:
#   MSB_REPO     repo root (default: the repo this script lives in)
#   STAGES       gate stages (default endpoints,harness -- the CI set)
#   MSB_PORT     server probe port (default 8766)
#   QDRANT_PORT  qdrant probe port (default 6333)
#
# Exit: 0 = all steps passed; 1 = any probe or the gate failed.

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO" || exit 2

MSB_PORT="${MSB_PORT:-8766}"
QDRANT_PORT="${QDRANT_PORT:-6333}"
STAGES="${STAGES:-endpoints,harness}"

fail=0

say() { echo "[harness-gate-dryrun] $*"; }

# start_best_effort <make-target> -- both idempotent start scripts exit 1
# when the service is already healthy, so failures are swallowed; the probe
# below is the real gate. Output keeps the reassuring "already running" line
# and any real error output from the start script, filtered only of make's
# redundant 'Error N' recap (pipefail keeps make's exit as the pipeline's).
start_best_effort() {
  make "$1" 2>&1 | sed '/^make\[[0-9]*\]: \*\*\* \[/d' || true
}

# probe <name> <url> <max_attempts> -- bounded retry loop matching the
# workflow's probes (10 attempts qdrant, 30 attempts server).
probe() {
  local name="$1" url="$2" tries="$3"
  local i=0
  until curl -fsS -m 2 "$url" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge "$tries" ]; then
      say "STEP FAILED: $name not healthy on $url after ${tries} attempts" >&2
      return 1
    fi
    sleep 1
  done
  say "$name healthy on $url"
  return 0
}

echo
echo "===== [1/3 ensure qdrant is up (:${QDRANT_PORT})] ====="
start_best_effort qdrant-start
probe "qdrant" "http://127.0.0.1:${QDRANT_PORT}/healthz" 10 || fail=$((fail + 1))

echo
echo "===== [2/3 ensure msb-v3 server is up (:${MSB_PORT})] ====="
start_best_effort server-start
probe "msb-v3" "http://127.0.0.1:${MSB_PORT}/health" 30 || fail=$((fail + 1))

echo
echo "===== [3/3 gate: make webcheck-all STAGES=${STAGES}] ====="
make webcheck-all STAGES="$STAGES" || fail=$((fail + 1))

echo
say "CI would upload: artifacts/harness-evidence-report.json + webcheck-all-*.log + webcheck-*/"
if [ "$fail" -eq 0 ]; then
  echo "harness-gate-dryrun: ALL STEPS PASSED"
  exit 0
fi
echo "harness-gate-dryrun: $fail step(s) FAILED" >&2
exit 1
