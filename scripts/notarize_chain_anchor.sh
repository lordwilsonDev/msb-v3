#!/usr/bin/env bash
set -euo pipefail

# Notarize the audit-chain anchor out-of-band.
#
# Appends a signed snapshot of the current chain tip to an append-only notary
# log OUTSIDE the repo (default: ~/msb-backups/chain-anchor-notary.jsonl), so
# an attacker who replaces the whole audit DB (T7) cannot also roll back the
# notary. Then pushes the log to a configured rclone remote (default gdrive:),
# which the attacker cannot reach. The remote push is best-effort: the local
# append must succeed, and a remote failure is reported loudly (non-zero exit)
# but never hides the local notarization.
#
# Env:
#   MSB_NOTARY_LOG     local append-only log (default ~/msb-backups/chain-anchor-notary.jsonl)
#   MSB_NOTARY_REMOTE  rclone remote:path (default gdrive:msb-v3/chain-anchor-notary.jsonl);
#                      empty = skip the remote push
#
# Usage:
#   ./scripts/notarize_chain_anchor.sh [<audit.db>]

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"

set -a
[ -f "$REPO/.env" ] && . "$REPO/.env"
set +a

export PATH="/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
export PYTHONPATH="$REPO/src:~/.local/lib/msb-v3"
export MSB_DB_PATH="${MSB_DB_PATH:-$REPO/data/msb_v3.db}"

DB="${1:-$REPO/data/uac/audit_chain.db}"
NOTARY="${MSB_NOTARY_LOG:-$HOME/msb-backups/chain-anchor-notary.jsonl}"
REMOTE="${MSB_NOTARY_REMOTE:-gdrive:msb-v3/chain-anchor-notary.jsonl}"

mkdir -p "$(dirname "$NOTARY")"

# 1) Append the signed snapshot locally (must succeed).
"$PY" -m msb_v3.uac.chain_anchor --notarize "$DB" --notary "$NOTARY"

# 2) Push out-of-band (best-effort but loud).
if [ -n "$REMOTE" ] && command -v rclone >/dev/null 2>&1; then
  if rclone copyto "$NOTARY" "$REMOTE" 2> >(sed 's/^/  [rclone] /' >&2); then
    echo "[notary] pushed to $REMOTE"
  else
    echo "[notary] WARNING: remote push FAILED — local notary log at $NOTARY is intact but not out-of-band" >&2
    exit 1
  fi
elif [ -n "$REMOTE" ]; then
  echo "[notary] WARNING: rclone not found — skipping remote push (local log still written)" >&2
  exit 1
fi

echo "[notary] OK: appended anchor snapshot -> $NOTARY"
