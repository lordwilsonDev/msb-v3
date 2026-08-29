#!/usr/bin/env bash
set -euo pipefail

# Portable: MSB_REPO / MSB_PYTHON override (CI sets these); defaults resolve
# sets them from the checkout + actions/setup-python).
REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
# Prefer the checkout's own venv when present (the M7 setup guide creates
# one: `python3 -m venv .venv && pip install -r requirements-*.lock`). An
# explicit MSB_PYTHON (CI) always wins; otherwise fall back to this machine's
# base python so existing launchd/standby installs keep working unchanged.
if [ -x "$REPO/.venv/bin/python" ] && [ -z "${MSB_PYTHON:-}" ]; then
  PY="$REPO/.venv/bin/python"
elif [ -n "${MSB_PYTHON:-}" ]; then
  PY="$MSB_PYTHON"
else
  PY="/opt/homebrew/Caskroom/miniforge/base/bin/python"
fi

# Load the repo .env (gitignored secrets) so the server resolves env the
# same way scripts/webcheck.sh does: env -> .env -> shipped default. Pre-
# exported env still wins because every export below uses ${VAR:-default}.
set -a
[ -f "$REPO/.env" ] && . "$REPO/.env"
set +a

unset VIRTUAL_ENV
export PATH="/opt/homebrew/Caskroom/miniforge/base/bin:$HOME/.local/bin:$PATH"
export PYTHONPATH="$REPO/src:~/.local/lib/msb-v3"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:8b}"
export MSB_DB_PATH="${MSB_DB_PATH:-$REPO/data/msb_v3.db}"
export MSB_HOST="${MSB_HOST:-127.0.0.1}"
export MSB_PORT="${MSB_PORT:-8766}"
export MSB_RELOAD="${MSB_RELOAD:-0}"
export MSB_RAG_API_KEY="${MSB_RAG_API_KEY:-}"
export MCP_BRIDGE_SECRET="${MCP_BRIDGE_SECRET:-}"
export MSB_MULTIMODAL_ENABLED="${MSB_MULTIMODAL_ENABLED:-1}"

log() { echo "[msb-v3] $*"; }

# --- source-license gate ------------------------------------------------------
# The repo is source-available: the server refuses to start without a
# license signed by the owner's key (scripts/lib/license.sh + docs/
# pull-signature-and-access.md). An anonymous pull is inert code — to run
# it you must fork the repo and obtain a license
# (scripts/request-access.sh).
#
# Explicit opt-outs for legitimate non-anonymous runs (same contract as
# msb_v3/__main__.py): MSB_CONTAINER=1 (Docker image, a build artifact of
# a LICENSED checkout) and MSB_CI=1 (hosted CI runners clone the owner's
# checkout and have no home-dir license). A bare pull has no license AND
# neither flag, so the source-distribution path stays inert.
# shellcheck source=lib/license.sh
if [ "${MSB_CONTAINER:-0}" = "1" ] || [ "${MSB_CI:-0}" = "1" ]; then
  log "source license gate skipped (declared CI/container run)"
else
  . "$REPO/scripts/lib/license.sh"
  set +e
  lstatus="$(license_status)"
  lrc=$?
  set -e
  if [ "$lrc" -ne 0 ] || [ "$lstatus" != "valid" ]; then
    log "ERROR: no valid source license ($lstatus)."
    log "  This code runs only under a license signed by the owner. Fork the repo"
    log "  and request one: bash scripts/request-access.sh"
    exit 1
  fi
  log "source license valid"
fi

log "starting msb-v3 host=$MSB_HOST port=$MSB_PORT model=$OLLAMA_MODEL"

# PM2-style single-process supervisor: restart on non-zero exit.
# set -e would otherwise kill this whole loop on the first crash/SIGTERM,
# since the loop body never reaches `code=$?` for a failing command.
while true; do
  set +e
  "$PY" -m msb_v3
  code=$?
  set -e
  log "process exited $code; restarting in 2s"
  sleep 2
done
