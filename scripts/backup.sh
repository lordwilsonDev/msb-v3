#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}" -m msb_v3.ops backup --keep 14
