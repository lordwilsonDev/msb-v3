#!/usr/bin/env bash
set -euo pipefail

# Unset Hermes venv if present; use miniforge explicitly.
unset VIRTUAL_ENV
export PATH="/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1
export PYTHONPATH="$REPO/src"
export MSB_RELOAD="${MSB_RELOAD:-0}"
export MSB_REASONING_SCORER=1

export MCP_BRIDGE_SECRET="8e174eff4420062607ccb6cb1c98997680cbf82385e290bb4b3208d8f15df621"
export MSB_RAG_API_KEY="${MSB_RAG_API_KEY:-07bd51761bde7dce3268473773cef30f6ded1062bd7351b33f50863d2d184277}"
exec python -m msb_v3
