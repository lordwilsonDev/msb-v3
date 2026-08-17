#!/usr/bin/env bash
set -euo pipefail

# Tight-interval chain-anchor verifier (launchd:
# com.lordwilson.chain-anchor-verify, StartInterval 600 = every 10 min).
# Verifies the LIVE audit chain against its external signed chain-tip anchor
# (the T7 fix). A daily-only check would leave a tamper live and trusted for
# up to ~24h; every-10-minutes bounds the undetected window to minutes.
# Healthy => one-line OK + state file. Any problem (stale anchor, whole-DB
# replacement, anchor tamper, missing key/anchor, broken internal chain,
# remote notary unreachable/diverged) => macOS notification + ALERT line +
# non-zero exit. Override the DB for testing: ./scripts/verify_chain_anchor.sh <path>

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"

set -a
[ -f "$REPO/.env" ] && . "$REPO/.env"
set +a

export PATH="/opt/homebrew/bin:/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
export PYTHONPATH="$REPO/src:~/.local/lib/msb-v3"
export MSB_DB_PATH="${MSB_DB_PATH:-$REPO/data/msb_v3.db}"

DB="${1:-$REPO/data/uac/audit_chain.db}"

ARGS=(--verify-daemon "$DB")
# Re-sign a benignly STALE anchor (newer records appended after the last
# anchor, e.g. by a keyless background process) instead of alerting; real
# problems (replacement / tamper / broken chain) still alert + exit 2.
ARGS+=(--auto-anchor)
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

# Off-box notary verification (#2): reads the REMOTE head (never the local
# last line) and cross-checks seq sets both ways — a local notary rollback is
# caught even when the local log AND the local anchor file were both replaced.
# Unreachable remote is fail-closed (REMOTE_UNREACHABLE => ALERT): the absence
# of off-box proof is never treated as health.
REMOTE_CHECK=$("$PY" -m msb_v3.uac.notary --verify "$DB" 2>&1) || {
  echo "ALERT chain_anchor remote-notary: $REMOTE_CHECK" >&2
  exit 2
}
echo "[verify] $REMOTE_CHECK"

exec "$PY" -m msb_v3.uac.chain_anchor "${ARGS[@]}"
