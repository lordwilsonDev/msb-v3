#!/usr/bin/env bash
set -euo pipefail

# request-access.sh — the fork-to-use flow for this repo.
#
# The server refuses to start without a source license signed by the
# owner's key. This script walks a contributor through the intended path:
#   1. If this checkout is the CANONICAL repo (not a fork), fork it
#      (gh repo fork; falls back to printed instructions when gh is not
#      available/authenticated).
#   2. Open an issue on the canonical repo requesting a license, naming the
#      fork, so the owner can run scripts/issue-license.sh <holder>.
#   3. Show how to install the license once received.
#
# Safe to re-run; never modifies the canonical repo except the request
# issue (which is the point).

CANONICAL="lordwilsonDev/msb-v3"
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/license.sh"
# shellcheck source=lib/license.sh
. "$LIB"

log() { echo "[request-access] $*"; }

# --- current status ----------------------------------------------------------
set +e
status="$(license_status)"
set -e
log "license status: $status"
[ "$status" = "valid" ] && { log "you already have a valid license — nothing to do."; exit 0; }

remote="$(git -C "$REPO" remote get-url origin 2>/dev/null || true)"
norm="$(printf '%s' "$remote" | sed -E 's#^git@([^:]+):#https://\1/#' | sed -E 's#^https?://##; s#\.git$##')"
log "origin: ${remote:-<none>}"

if [ "$norm" = "$CANONICAL" ]; then
  log "this checkout is the canonical repo itself. Fork it first:"
  if command -v gh >/dev/null 2>&1; then
    gh repo fork "$CANONICAL" --remote=true 2>&1 | sed 's/^/  /' || true
  else
    echo "  install + auth gh, then: gh repo fork $CANONICAL --remote=true"
    echo "  (or fork via the GitHub web UI and clone YOUR fork)"
  fi
fi

# --- request a license --------------------------------------------------------
holder="${1:-}"
if [ -z "$holder" ]; then
  holder="$(git config --get user.name 2>/dev/null || true)"
  [ -n "$holder" ] || holder="$(command -v gh >/dev/null 2>&1 && gh api user --jq .login 2>/dev/null || true)"
fi
[ -n "$holder" ] || holder="$(id -un)"

fork_url="https://github.com/$(printf '%s' "$norm" | cut -d/ -f1)/$(printf '%s' "$norm" | cut -d/ -f2)"
log "requesting a license for '$holder'"
if command -v gh >/dev/null 2>&1; then
  gh issue create -R "$CANONICAL" \
    --title "Source license request: $holder" \
    --body "Please issue a source license for **$holder**.

- fork: $fork_url
- requested: $(date +%F)" 2>&1 | sed 's/^/  /' || log "could not open the issue (see manual steps below)"
else
  echo "  open an issue at https://github.com/$CANONICAL/issues/new"
  echo "  title: Source license request: $holder"
  echo "  body:  fork = $fork_url"
fi

log "once the owner runs 'scripts/issue-license.sh $holder', save the license at:"
echo "  $LICENSE_FILE"
log "then verify: bash scripts/verify-license.sh"
