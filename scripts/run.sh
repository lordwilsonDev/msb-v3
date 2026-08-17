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
export PATH="/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
export PYTHONPATH="$REPO/src:~/.local/lib/msb-v3"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:8b}"
export MSB_DB_PATH="${MSB_DB_PATH:-$REPO/data/msb_v3.db}"
export MSB_HOST="${MSB_HOST:-127.0.0.1}"
export MSB_PORT="${MSB_PORT:-8766}"
export MSB_RELOAD="${MSB_RELOAD:-0}"
export MSB_RAG_API_KEY="${MSB_RAG_API_KEY:-}"
export MCP_BRIDGE_SECRET="${MCP_BRIDGE_SECRET:-}"

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
