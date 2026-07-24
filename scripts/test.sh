#!/usr/bin/env bash
set -euo pipefail

REPO="/Users/lordwilson/msb-v3"
PY="/opt/homebrew/Caskroom/miniforge/base/bin/python"

unset VIRTUAL_ENV
export PATH="/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
export PYTHONPATH="$REPO/src"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:latest}"
export MSB_DB_PATH="${MSB_DB_PATH:-$REPO/data/msb_v3.db}"

cd "$REPO"
exec "$PY" -m pytest -q "$@"
