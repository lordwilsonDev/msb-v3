#!/usr/bin/env bash
set -euo pipefail

# ops-audit.sh — full ops self-audit in one command.
#
# Runs the complete verification stack: the ops-script regression suite,
# the pull-signature ledger (with per-witness attribution), and the source
# license. Exits non-zero if any check fails so the backup watchdog alerts.
# On failure it also fires out-of-band alert channels (macOS notification +
# optional email + optional Telegram — see scripts/lib/alert.sh). When
# MSB_PUBLISH_AUDIT=1 the dated audit report is committed and pushed to
# origin (self-publishing evidence) via scripts/publish-audit.sh.
#
# Driven weekly (Sun 06:50) by com.lordwilson.ops-audit — the last step of
# the Sunday cascade, so a regression in ANY ops script, a broken signature
# trail, a license problem, or a failed publish surfaces as an alert instead
# of silently drifting.
#
# Run: bash scripts/ops-audit.sh   (or: make ops-audit)
# Overrides: MSB_OPS_AUDIT_LOG, MSB_AUDIT_SKIP_SUITE (1 skips the regression
# suite — test hook), MSB_PUBLISH_AUDIT, MSB_ALERT_EMAIL,
# MSB_TELEGRAM_BOT_TOKEN, MSB_TELEGRAM_CHAT_ID, plus the per-check overrides
# passed through (MSB_PULL_LEDGER, MSB_PULL_ALLOWED, MSB_LICENSE_FILE, ...).

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${MSB_OPS_AUDIT_LOG:-$REPO/logs/ops-audit.log}"
mkdir -p "$(dirname "$LOG")"
log() { echo "[ops-audit] $(date '+%F %T') $*" | tee -a "$LOG"; }

# shellcheck source=lib/alert.sh
. "$REPO/scripts/lib/alert.sh"

fails=0
step() { echo; echo "== $1 =="; }

SUITE=pass LEDGER=pass LICENSE=pass SCHEMA=pass
step "ops-script regression suite (bash 3.2, scratch dirs)"
if [ "${MSB_AUDIT_SKIP_SUITE:-0}" = "1" ]; then
  SUITE=skipped
  log "regression suite skipped (MSB_AUDIT_SKIP_SUITE=1)"
else
  bash "$REPO/scripts/test-ops.sh" || { fails=$((fails + 1)); SUITE=fail; log "FAIL: regression suite"; }
fi

step "pull-signature ledger (per-witness attribution)"
bash "$REPO/scripts/verify-pull-signatures.sh" || { fails=$((fails + 1)); LEDGER=fail; log "FAIL: pull-signature ledger"; }

step "source license"
bash "$REPO/scripts/verify-license.sh" || { fails=$((fails + 1)); LICENSE=fail; log "FAIL: source license"; }

step "db schema versioning (H9 — every data/*.db stamped)"
"${MSB_PYTHON:-python3}" "$REPO/scripts/stamp-schemas.py" --check \
  || { fails=$((fails + 1)); SCHEMA=fail; log "FAIL: unstamped SQLite DB (run scripts/stamp-schemas.py)"; }

step "ops status (informational)"
bash "$REPO/scripts/ops-status.sh" >/dev/null 2>&1 || true

# --- self-publish the audit report (evidence lives in the repo) --------------
SUMMARY="suite=$SUITE ledger=$LEDGER license=$LICENSE schema=$SCHEMA"
if [ "${MSB_PUBLISH_AUDIT:-0}" = "1" ]; then
  step "publish audit report"
  if MSB_AUDIT_SUMMARY="$SUMMARY" bash "$REPO/scripts/publish-audit.sh"; then
    log "publish OK"
  else
    fails=$((fails + 1))
    log "FAIL: audit report publish"
  fi
fi

echo
if [ "$fails" -eq 0 ]; then
  log "AUDIT-OK — all checks green"
  exit 0
fi
log "AUDIT-FAIL — $fails check(s) failed (see above)"
# out-of-band alert: watchdog covers the exit code; email/telegram make the
# failure impossible to ignore even if local notifications are suppressed.
notify_all "OPS AUDIT FAILED — msb-v3" "$fails check(s) failed at $(date '+%F %T'). Log: $LOG"
exit 1
