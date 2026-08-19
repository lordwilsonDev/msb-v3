#!/usr/bin/env bash
set -euo pipefail

# ops-audit.sh — full ops self-audit in one command.
#
# Runs the complete verification stack: the ops-script regression suite,
# the pull-signature ledger, and the source license. Exits non-zero if any
# check fails so the backup watchdog alerts. Driven weekly (Sun 06:50) by
# com.lordwilson.ops-audit — the last step of the Sunday cascade, so a
# regression in ANY ops script, a broken signature trail, or a license
# problem surfaces as an alert instead of silently drifting.
#
# Run: bash scripts/ops-audit.sh   (or: make ops-audit)
# Override: MSB_OPS_AUDIT_LOG

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${MSB_OPS_AUDIT_LOG:-$REPO/logs/ops-audit.log}"
mkdir -p "$(dirname "$LOG")"
log() { echo "[ops-audit] $(date '+%F %T') $*" | tee -a "$LOG"; }

fails=0
step() { echo; echo "== $1 =="; }

step "ops-script regression suite (bash 3.2, scratch dirs)"
bash "$REPO/scripts/test-ops.sh" || { fails=$((fails + 1)); log "FAIL: regression suite"; }

step "pull-signature ledger"
bash "$REPO/scripts/verify-pull-signatures.sh" || { fails=$((fails + 1)); log "FAIL: pull-signature ledger"; }

step "source license"
bash "$REPO/scripts/verify-license.sh" || { fails=$((fails + 1)); log "FAIL: source license"; }

step "ops status (informational)"
bash "$REPO/scripts/ops-status.sh" >/dev/null 2>&1 || true

echo
if [ "$fails" -eq 0 ]; then
  log "AUDIT-OK — all checks green"
  exit 0
fi
log "AUDIT-FAIL — $fails check(s) failed (see above)"
exit 1
