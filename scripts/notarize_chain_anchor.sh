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

# Single instance per host. Two notarize() runs racing DO corrupt the off-box
# notary, measurably: each computes ``seq = len(local lines) + 1`` and its
# object name from its own notarized_at, so two concurrent runs can both claim
# the same seq, and the next run's backfill — which dedupes the remote BY SEQ
# ONLY — re-pushes the same (seq, ts) name twice. Google Drive permits
# same-name siblings rather than overwriting by name, so the twice-pushed
# object arrives as one name resolving to TWO objects; ``rclone cat`` then
# concatenates them and verify() reads one valid entry pasted onto itself.
# Measured live on 2026-10-11: six such names (2026-09-26 → 10-10), each read
# back as two byte-identical halves — labelled "not JSON", which reads as
# tampering, until the verdict learned to name a duplicate. This lock is the
# root cause's choke point, not a workaround for it.
LOCK_DIR="${MSB_NOTARY_LOCK:-$REPO/data}" 
LOCK="$LOCK_DIR/notarize.lock"
mkdir -p "$LOCK_DIR"
if ! mkdir "$LOCK" 2>/dev/null; then
  OWNER_PID="$(cat "$LOCK/pid" 2>/dev/null || echo unknown)"
  echo "[notary] another notarization holds the lock (pid $OWNER_PID); exiting so it cannot double-push. If this recurs, two notary launchd jobs may share one schedule — check com.blackswanlabz.msb-v3.notary and com.lordwilson.msb-chain-notary." >&2
  exit 0
fi
trap 'rm -rf "$LOCK"' EXIT
printf '%s\n' "$$" > "$LOCK/pid"

# Local append must succeed (fail-closed); remote push failure is loud but the
# local notary log stays intact (the CLI exits 1 in that case).
OUT=$("$PY" -m msb_ledger.notary --notarize "$DB" 2>&1) || {
  echo "$OUT" >&2
  echo "[notary] WARNING: notarization incomplete — see above; the LOCAL notary log may be intact but the off-box push did not complete" >&2
  exit 1
}
echo "[notary] OK: $(echo "$OUT" | tr '\n' ' ')"
