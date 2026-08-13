#!/usr/bin/env bash
set -euo pipefail

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"

unset VIRTUAL_ENV
export PATH="/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
export PYTHONPATH="$REPO/src"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:latest}"
export MSB_DB_PATH="${MSB_DB_PATH:-$REPO/data/msb_v3.db}"

cd "$REPO"
# -rs: report skip reasons so intentional skips (MSB_LIVE acceptance,
# seeded-run-ledger) are auditable in every suite run, not just the count.
exec "$PY" -m pytest -q -rs "$@"
