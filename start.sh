#!/usr/bin/env bash
set -euo pipefail

# Unset Hermes venv if present; use miniforge explicitly.
unset VIRTUAL_ENV
export PATH="/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1
export PYTHONPATH="$REPO/src"
export MSB_RELOAD="${MSB_RELOAD:-0}"
export MSB_REASONING_SCORER=1

# Load gitignored secrets from .env so no credential is committed to the repo.
# Pre-exported env still wins because every export below uses ${VAR:-default}.
set -a
[ -f "$REPO/.env" ] && . "$REPO/.env"
set +a

if [ -z "${MCP_BRIDGE_SECRET:-}" ]; then
  echo "[start] ERROR: MCP_BRIDGE_SECRET is not set (export it or define it in $REPO/.env)" >&2
  exit 1
fi
export MCP_BRIDGE_SECRET="${MCP_BRIDGE_SECRET}"
export MSB_RAG_API_KEY="${MSB_RAG_API_KEY:-}"
exec python -m msb_v3
