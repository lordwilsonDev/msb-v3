#!/usr/bin/env bash
# close-out-gate.sh — the close-out definition of done (FR-4.2 / AC-4.1).
#
# Runs the full battery in one command and exits non-zero on ANY failure:
#   1. lint   — ruff + mypy (all of src) + verify-claims + policy drift gate
#   2. pytest — full suite with coverage floor (--cov-fail-under=70), against
#      a freshly booted server (same discipline as CI)
#   3. pip-audit — blocking CVE scan over the installed environment
#   4. docker  — build the real runtime image from a clean checkout and prove
#      GET /health answers from the container (no host Python)
#
# A leg may be skipped ONLY explicitly: MSB_CLOSE_OUT_SKIP=<comma-separated
# leg names> (e.g. MSB_CLOSE_OUT_SKIP=docker on a machine with no daemon).
# Skipped legs are printed loudly — this gate never silently drops a check.
#
#   bash scripts/close-out-gate.sh
#   MSB_CLOSE_OUT_SKIP=docker bash scripts/close-out-gate.sh
#   MSB_CLOSE_OUT_SKIP=pytest,docker bash scripts/close-out-gate.sh   # CI aggregator
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"
cd "$REPO"
export PYTHONPATH="$REPO/src"

SKIP="${MSB_CLOSE_OUT_SKIP:-}"
skip() { # leg
  case ",$SKIP," in
    *",$1,"*) return 0 ;;
    *) return 1 ;;
  esac
}

PASS=0
FAIL=0
SKIPPED=0
declare -a FAILED_LEGS=()

leg() { # name result
  local name="$1" result="$2"
  case "$result" in
    0) PASS=$((PASS + 1)); echo "[close-out] PASS  $name" ;;
    *) FAIL=$((FAIL + 1)); FAILED_LEGS+=("$name"); echo "[close-out] FAIL  $name" ;;
  esac
}

echo "[close-out] starting at $(date '+%F %T') — repo $REPO"

# --- 1. lint: ruff + mypy (all of src) + claims + policy drift ---------------
if skip lint; then
  echo "[close-out] SKIP  lint (MSB_CLOSE_OUT_SKIP)"
  SKIPPED=$((SKIPPED + 1))
else
  "$PY" -m ruff check src/ tests/ >/tmp/close-out-ruff.log 2>&1 \
    && "$PY" -m mypy src >/tmp/close-out-mypy.log 2>&1 \
    && "$PY" scripts/verify-claims.py >/tmp/close-out-claims.log 2>&1 \
    && MSB_PYTHON="$PY" bash scripts/ci-policy-gate.sh >/tmp/close-out-policy.log 2>&1
  leg lint $?
fi

# --- 2. pytest: full suite + coverage floor, against a booted server ---------
if skip pytest; then
  echo "[close-out] SKIP  pytest (MSB_CLOSE_OUT_SKIP)"
  SKIPPED=$((SKIPPED + 1))
else
  bash scripts/seed-research-runtime.sh >/tmp/close-out-seed.log 2>&1 || true
  # Free :8766 for the gate's own server, then boot under the app supervisor
  # contract so the restart path is the one being exercised.
  lsof -t -iTCP:8766 -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
  "$PY" -m msb_v3 >/tmp/close-out-server.log 2>&1 &
  SRV=$!
  trap 'kill $SRV 2>/dev/null || true; lsof -t -iTCP:8766 -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true' EXIT
  up=0
  for _ in $(seq 1 60); do
    if curl -sf -o /dev/null http://127.0.0.1:8766/health; then up=1; break; fi
    sleep 1
  done
  if [ "$up" != "1" ]; then
    echo "[close-out] FAIL  pytest (server failed to boot — log follows)"
    tail -20 /tmp/close-out-server.log
    leg pytest 1
  else
    "$PY" -m pytest -q tests/ --cov=msb_v3 --cov-report=term --cov-fail-under=70 >/tmp/close-out-pytest.log 2>&1
    leg pytest $?
  fi
  kill "$SRV" 2>/dev/null || true
  wait "$SRV" 2>/dev/null || true
  trap - EXIT
  lsof -t -iTCP:8766 -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null || true
fi

# --- 3. pip-audit: blocking CVE scan ----------------------------------------
if skip pip-audit; then
  echo "[close-out] SKIP  pip-audit (MSB_CLOSE_OUT_SKIP)"
  SKIPPED=$((SKIPPED + 1))
elif ! command -v pip-audit >/dev/null 2>&1; then
  echo "[close-out] FAIL  pip-audit (pip-audit not installed — pip install pip-audit)"
  leg pip-audit 1
else
  pip freeze --exclude-editable >/tmp/close-out-audit-requirements.txt 2>/dev/null
  pip-audit -r /tmp/close-out-audit-requirements.txt --strict >/tmp/close-out-audit.log 2>&1
  leg pip-audit $?
fi

# --- 4. docker: build the real image + container /health smoke --------------
if skip docker; then
  echo "[close-out] SKIP  docker (MSB_CLOSE_OUT_SKIP)"
  SKIPPED=$((SKIPPED + 1))
elif ! command -v docker >/dev/null 2>&1; then
  echo "[close-out] FAIL  docker (docker not installed)"
  leg docker 1
else
  IMG="msb-v3:close-out-$(date +%s)"
  if docker build -t "$IMG" . >/tmp/close-out-docker-build.log 2>&1; then
    if docker run -d -p 8766:8766 --name msb-close-out "$IMG" >/dev/null 2>&1; then
      trap 'docker rm -f msb-close-out >/dev/null 2>&1 || true' EXIT
      ok=0
      for _ in $(seq 1 60); do
        if curl -sf http://127.0.0.1:8766/health >/dev/null 2>&1; then ok=1; break; fi
        if ! docker inspect -f '{{.State.Running}}' msb-close-out 2>/dev/null | grep -q true; then break; fi
        sleep 1
      done
      if [ "$ok" = "1" ]; then
        leg docker 0
      else
        echo "[close-out] container never served /health — log follows"
        docker logs msb-close-out 2>&1 | tail -20
        leg docker 1
      fi
      docker rm -f msb-close-out >/dev/null 2>&1 || true
      trap - EXIT
    else
      echo "[close-out] FAIL  docker (docker run failed — log follows)"
      tail -20 /tmp/close-out-docker-build.log
      leg docker 1
    fi
  else
    echo "[close-out] FAIL  docker (build failed — log follows)"
    tail -30 /tmp/close-out-docker-build.log
    leg docker 1
  fi
fi

echo
echo "[close-out] done at $(date '+%F %T') — PASS=$PASS FAIL=$FAIL SKIPPED=$SKIPPED"
if [ "${#FAILED_LEGS[@]}" -gt 0 ]; then
  echo "[close-out] FAILED LEGS: ${FAILED_LEGS[*]}"
  exit 1
fi
exit 0
