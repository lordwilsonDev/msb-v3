#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/lordwilson/msb-v3"
PY="/opt/homebrew/Caskroom/miniforge/base/bin/python"

unset VIRTUAL_ENV
export PATH="/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
export PYTHONPATH="$REPO/src"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:latest}"
export MSB_DB_PATH="${MSB_DB_PATH:-$REPO/data/msb_v3.db}"
export MSB_HOST="${MSB_HOST:-0.0.0.0}"
export MSB_PORT="${MSB_PORT:-8766}"
export MSB_RELOAD="${MSB_RELOAD:-0}"

log() { echo "[msb-v3] $*"; }

log "starting msb-v3 host=$MSB_HOST port=$MSB_PORT model=$OLLAMA_MODEL"

# PM2-style single-process supervisor: restart on non-zero exit.
while true; do
  "$PY" -m msb_v3
  code=$?
  log "process exited $code; restarting in 2s"
  sleep 2
done
