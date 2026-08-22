#!/usr/bin/env bash
set -euo pipefail

# Phase 4 autonomous repair loop (launchd:
# com.blackswanlabz.msb-v3.auto-repair, StartInterval 600 = every 10 min).
# Runs one bounded self-repair cycle: scan → diagnose → propose (deduped) →
# execute AUTO-authority plans only (requeue_wake, reanchor_chain). OPERATOR
# plans stay in awaiting_approval — the loop never approves. The kill switch
# and verify-before-trust gate every execution.
#
# Disable without touching launchd: MSB_AUTO_REPAIR_ENABLED=0 in .env
# (the loop checks the same flag, belt and braces).

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"

set -a
[ -f "$REPO/.env" ] && . "$REPO/.env"
set +a

export PATH="/opt/homebrew/bin:/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
export PYTHONPATH="$REPO/src:~/.local/lib/msb-v3"
export MSB_DB_PATH="${MSB_DB_PATH:-$REPO/data/msb_v3.db}"

[ "${MSB_AUTO_REPAIR_ENABLED:-1}" = "1" ] || {
    echo "auto-repair disabled (MSB_AUTO_REPAIR_ENABLED=0)"
    exit 0
}

exec "$PY" -m msb_v3.ops.auto_repair run
