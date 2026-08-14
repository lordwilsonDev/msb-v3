#!/usr/bin/env bash
set -euo pipefail

# Daily chain-anchor verifier (launchd: com.lordwilson.chain-anchor-verify).
# Verifies the LIVE audit chain against its external signed chain-tip anchor
# (the T7 fix). Healthy => one-line OK + state file. Any problem (stale
# anchor, whole-DB replacement, anchor tamper, missing key/anchor, broken
# internal chain) => macOS notification + ALERT line + non-zero exit.
# Override the DB for testing: ./scripts/verify_chain_anchor.sh <path>

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"

set -a
[ -f "$REPO/.env" ] && . "$REPO/.env"
set +a

export PATH="/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
export PYTHONPATH="$REPO/src:~/.local/lib/msb-v3"
export MSB_DB_PATH="${MSB_DB_PATH:-$REPO/data/msb_v3.db}"

DB="${1:-$REPO/data/uac/audit_chain.db}"

ARGS=(--verify-daemon "$DB")
if [ "${MSB_CHAIN_ANCHOR_NO_NOTIFY:-0}" = "1" ]; then
    ARGS+=(--no-notify)
fi

# When a notary log is configured, the daily verify also checks the LAST
# out-of-band entry against the live chain — so a whole-DB rollback that
# also replaced the local anchor file is still caught by the notary.
if [ -n "${MSB_NOTARY_LOG:-}" ]; then
  NOTARY_CHECK=$("$PY" -m msb_v3.uac.chain_anchor --verify-notary "$DB" --notary "$MSB_NOTARY_LOG" 2>&1) || {
    echo "ALERT chain_anchor notary: $NOTARY_CHECK" >&2
    exit 2
  }
  echo "[verify] $NOTARY_CHECK"
fi

exec "$PY" -m msb_v3.uac.chain_anchor "${ARGS[@]}"
