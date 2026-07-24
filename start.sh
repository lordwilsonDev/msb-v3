#!/usr/bin/env bash
set -euo pipefail

# Unset Hermes venv if present; use miniforge explicitly.
unset VIRTUAL_ENV
export PATH="/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
export PYTHONPATH="/Users/lordwilson/msb-v3/src"
export MSB_RELOAD="${MSB_RELOAD:-0}"
export MSB_REASONING_SCORER=1

exec python -m msb_v3
