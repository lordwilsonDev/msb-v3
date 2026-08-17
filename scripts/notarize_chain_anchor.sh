#!/usr/bin/env bash
set -euo pipefail

# Notarize the audit-chain anchor out-of-band.
#
# Appends a signed snapshot of the current chain tip to an append-only notary
# log OUTSIDE the repo (default: ~/msb-backups/chain-anchor-notary.jsonl), so
# an attacker who replaces the whole audit DB (T7) cannot also roll back the
# notary. Each entry is ALSO pushed off-box as its OWN object
# ({seq}-{ts}.line) under MSB_NOTARY_REMOTE via rclone — per-object, so a
# remote cannot be silently shrunk by re-pushing (missing seqs are detected)
# and overwrites break the signature. When MSB_TSA_URL is set, each entry
# carries an RFC 3161 timestamp proof (third-party WHEN).
#
# The remote push is fail-closed but never hides the local append: a failed
# push exits 1 loudly (the local notary log is intact), exactly the old
# "best-effort but loud" contract.
#
# Env:
#   MSB_NOTARY_LOG     local append-only JSONL (default ~/msb-backups/chain-anchor-notary.jsonl)
#   MSB_NOTARY_REMOTE  rclone remote DIRECTORY (default gdrive:msb-v3/chain-anchor-notary);
#                      empty/"none" = skip the remote push
#   MSB_TSA_URL        RFC 3161 TSA endpoint (empty = receive-time only)
#
# Usage:
#   ./scripts/notarize_chain_anchor.sh [<audit.db>]

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"

set -a
[ -f "$REPO/.env" ] && . "$REPO/.env"
set +a

export PATH="/opt/homebrew/bin:/opt/homebrew/Caskroom/miniforge/base/bin:$PATH"
export PYTHONPATH="$REPO/src:~/.local/lib/msb-v3"
export MSB_DB_PATH="${MSB_DB_PATH:-$REPO/data/msb_v3.db}"

DB="${1:-$REPO/data/uac/audit_chain.db}"

# Local append must succeed (fail-closed); remote push failure is loud but the
# local notary log stays intact (the CLI exits 1 in that case).
OUT=$("$PY" -m msb_v3.uac.notary --notarize "$DB" 2>&1) || {
  echo "$OUT" >&2
  echo "[notary] WARNING: notarization incomplete — see above; the LOCAL notary log may be intact but the off-box push did not complete" >&2
  exit 1
}
echo "[notary] OK: $(echo "$OUT" | tr '\n' ' ')"
