#!/usr/bin/env bash
set -euo pipefail

# publish-audit.sh — write today's ops-audit report into audit/ and publish it.
#
# Self-publishing evidence: every weekly audit leaves a dated report in the
# repo's audit/ directory, committed (signed + DCO) and pushed to origin —
# so the record of what passed/failed lives in the repository itself, not
# just on this machine.
#
# Behavior:
#   * Report is always written to audit/YYYY-MM-DD_audit.md.
#   * Commit+push happen ONLY when MSB_PUBLISH_AUDIT=1 (the launchd agent
#     com.lordwilson.ops-audit sets it). Otherwise: report written, exit 0.
#   * --dry-run  writes the report and stops (no git operations).
#   * MSB_AUDIT_SUMMARY carries the audit's precomputed verdicts (set by
#     ops-audit.sh, so checks run once); when unset the script self-runs the
#     three checks to build the report.
#
# Exit: 0 on success / dry-run / nothing-to-commit; 1 if commit or push fails
# (ops-audit counts that as a failed check -> watchdog alerts).
#
# Overrides: MSB_AUDIT_DIR, MSB_AUDIT_SUMMARY, MSB_PUBLISH_AUDIT,
#            MSB_PUBLISH_LOG, MSB_PULL_LEDGER, MSB_PULL_ALLOWED,
#            MSB_LICENSE_FILE, MSB_LICENSE_AUTHORIZED.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUDIT_DIR="${MSB_AUDIT_DIR:-$REPO/audit}"
LOG="${MSB_PUBLISH_LOG:-$REPO/logs/ops-audit.log}"
mkdir -p "$AUDIT_DIR" "$(dirname "$LOG")"
log() { echo "[publish-audit] $(date '+%F %T') $*" | tee -a "$LOG"; }

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

STAMP="$(date '+%Y-%m-%d %H:%M')"
FILE="$AUDIT_DIR/$(date '+%Y-%m-%d')_audit.md"

# --- build the summary (precomputed by ops-audit, or self-check) -------------
if [ -n "${MSB_AUDIT_SUMMARY:-}" ]; then
  SUMMARY="$MSB_AUDIT_SUMMARY"
else
  suite=$(bash "$REPO/scripts/test-ops.sh" 2>&1 | grep -E "^=== result:" | tail -1 || echo "suite=UNKNOWN")
  ledger=$(bash "$REPO/scripts/verify-pull-signatures.sh" 2>&1 | tail -1 || echo "ledger=FAIL")
  lic=$(bash "$REPO/scripts/verify-license.sh" 2>&1 | tail -1 || echo "license=FAIL")
  SUMMARY="suite=$(echo "$suite" | sed 's/=== result: //') | $ledger | $lic"
fi

# --- compose the report -------------------------------------------------------
{
  echo "# msb-v3 ops audit — $STAMP"
  echo
  echo "**Summary:** $SUMMARY"
  echo
  echo "## Checks"
  echo
  echo "- Regression suite + ledger + license verdicts: \`$SUMMARY\`"
  echo
  echo "## Live ops status"
  echo
  echo '```'
  bash "$REPO/scripts/ops-status.sh" 2>&1 | head -80 || echo "(ops-status unavailable)"
  echo '```'
  echo
  echo "## Disk"
  echo
  echo '```'
  df -h / | tail -1
  echo '```'
} > "$FILE"
log "report written: $FILE"

[ "$DRY" = 1 ] && { echo "[publish-audit] dry-run — no git operations performed"; exit 0; }

# --- commit + push (only when enabled and targeting the repo's own audit/) ---
if [ "${MSB_PUBLISH_AUDIT:-0}" != "1" ]; then
  log "publish disabled (MSB_PUBLISH_AUDIT != 1) — report kept locally"
  exit 0
fi
if [ "$AUDIT_DIR" != "$REPO/audit" ]; then
  log "publish skipped: MSB_AUDIT_DIR override ($AUDIT_DIR) is not the repo audit/ dir"
  exit 0
fi

branch="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
git -C "$REPO" add -- audit/ || { log "FAIL: git add"; exit 1; }
if git -C "$REPO" diff --cached --quiet; then
  log "nothing to commit (report unchanged) — not publishing"
  exit 0
fi
# signed (-S is implied by commit.gpgsign) + DCO trailer (-s)
git -C "$REPO" commit -s -m "audit: $(date '+%Y-%m-%d') — $SUMMARY" \
  || { log "FAIL: git commit"; exit 1; }
log "committed audit report (signed + DCO)"
if git -C "$REPO" push origin "$branch"; then
  log "pushed audit report to origin/$branch"
else
  log "FAIL: git push origin $branch (gate may have blocked, or network down)"
  exit 1
fi
