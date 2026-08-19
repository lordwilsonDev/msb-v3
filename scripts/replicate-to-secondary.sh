#!/usr/bin/env bash
set -euo pipefail

# replicate-to-secondary.sh — mirror the repo (including .git, so the
# signed history + audit trail travel with it) to a second node.
#
# MSB_REPLICATION_TARGET is either:
#   user@host:/path      -> rsync over ssh (BatchMode, 5s connect timeout)
#   /local/path          -> plain local rsync (testing / same-machine)
# Unset/empty -> logs a note and exits 0 (not configured yet — a
# scheduled agent with no secondary is a config state, not a failure).
# Configured but unreachable -> exits 1, so the backup watchdog alerts:
# once you declare a secondary, losing it must be loud.
#
# Driven Sun 07:05 by com.lordwilson.replicate.
#
# Overrides: MSB_REPLICATION_TARGET, MSB_REPLICATION_LOG.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${MSB_REPLICATION_TARGET:-}"
LOG="${MSB_REPLICATION_LOG:-$REPO/logs/replicate.log}"
mkdir -p "$(dirname "$LOG")"
log() { echo "[replicate] $(date '+%F %T') $*" | tee -a "$LOG"; }

if [ -z "$TARGET" ]; then
  log "no replication target (MSB_REPLICATION_TARGET unset) — skipping (exit 0)"
  exit 0
fi

case "$TARGET" in
  *@*:*)
    log "replicating to remote: $TARGET"
    if rsync -a --delete -e "ssh -o BatchMode=yes -o ConnectTimeout=5" \
        "$REPO/" "$TARGET/" 2>/dev/null; then
      log "replication OK: $TARGET"
    else
      log "FAIL: remote unreachable or rsync failed: $TARGET"
      exit 1
    fi
    ;;
  *)
    if [ ! -d "$TARGET" ]; then
      log "FAIL: local target $TARGET does not exist"
      exit 1
    fi
    log "replicating to local dir: $TARGET"
    if rsync -a --delete "$REPO/" "$TARGET/" 2>/dev/null; then
      log "replication OK: $TARGET"
    else
      log "FAIL: rsync failed to $TARGET"
      exit 1
    fi
    ;;
esac
