#!/usr/bin/env bash
set -euo pipefail

VENV="/Users/lordwilson/msb-v3/.venv"
REPO="/Users/lordwilson/msb-v3"
SRC="$REPO/src"

unset VIRTUAL_ENV
export PATH="$VENV/bin:/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
export PYTHONPATH="$SRC"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:latest}"
export MSB_DB_PATH="${MSB_DB_PATH:-$REPO/data/msb_v3.db}"

exec "$VENV/bin/python" -m pytest -q "$@"
