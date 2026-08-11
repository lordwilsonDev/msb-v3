#!/usr/bin/env bash
set -euo pipefail

# Portable: MSB_REPO / MSB_PYTHON override (CI sets these); defaults resolve
# sets them from the checkout + actions/setup-python).
REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"

# Load the repo .env (gitignored secrets) so the server resolves env the
# same way scripts/webcheck.sh does: env -> .env -> shipped default. Pre-
# exported env still wins because every export below uses ${VAR:-default}.
set -a
[ -f "$REPO/.env" ] && . "$REPO/.env"
set +a

unset VIRTUAL_ENV
export PATH="/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
export PYTHONPATH="$REPO/src:~/.local/lib/msb-v3"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:8b}"
export MSB_DB_PATH="${MSB_DB_PATH:-$REPO/data/msb_v3.db}"
export MSB_HOST="${MSB_HOST:-127.0.0.1}"
export MSB_PORT="${MSB_PORT:-8766}"
export MSB_RELOAD="${MSB_RELOAD:-0}"
export MSB_RAG_API_KEY="${MSB_RAG_API_KEY:-07bd51761bde7dce3268473773cef30f6ded1062bd7351b33f50863d2d184277}"
export MCP_BRIDGE_SECRET="${MCP_BRIDGE_SECRET:-8e174eff4420062607ccb6cb1c98997680cbf82385e290bb4b3208d8f15df621}"

log() { echo "[msb-v3] $*"; }

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
