#!/usr/bin/env bash
set -euo pipefail

# Unset Hermes venv if present; use miniforge explicitly.
unset VIRTUAL_ENV
export PATH="/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
export PYTHONPATH="/Users/lordwilson/msb-v3/src"
export MSB_RELOAD="${MSB_RELOAD:-0}"
export MSB_REASONING_SCORER=1

export MCP_BRIDGE_SECRET="8e174eff4420062607ccb6cb1c98997680cbf82385e290bb4b3208d8f15df621"
exec python -m msb_v3
