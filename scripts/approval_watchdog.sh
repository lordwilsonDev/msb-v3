#!/usr/bin/env bash
set -euo pipefail

# Approval-ledger watchdog runner (launchd: com.lordwilson.vesta-approval-watchdog).
# Mirrors run.sh env loading so the watchdog sees the same config as the live
# service, then voids any APPROVED approvals whose task never reached a
# terminal state (the dangling-approval gap found by MSB-GOV-EVAL-001 §10).
# Dry-run inspection: MSB_WATCHDOG_DRY_RUN=1 ./scripts/approval_watchdog.sh

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"

set -a
[ -f "$REPO/.env" ] && . "$REPO/.env"
set +a

export PATH="/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
export PYTHONPATH="$REPO/src:~/.local/lib/msb-v3"
export MSB_DB_PATH="${MSB_DB_PATH:-$REPO/data/msb_v3.db}"

ARGS=(--operator watchdog)
if [ "${MSB_WATCHDOG_DRY_RUN:-0}" = "1" ]; then
    ARGS+=(--dry-run)
fi

exec "$PY" -m msb_v3.vesta.approval_watchdog "${ARGS[@]}"
