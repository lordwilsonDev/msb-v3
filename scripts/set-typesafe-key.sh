#!/usr/bin/env bash
# Idempotent TYPESAFE_API_KEY setup (TypeSafe AI — Jev / System One decisions).
#
#   bash scripts/set-typesafe-key.sh status                  # report set/unset (never prints it)
#   bash scripts/set-typesafe-key.sh set                     # prompt, input not echoed
#   TYPESAFE_API_KEY=... bash scripts/set-typesafe-key.sh set   # non-interactive
#
# Why a script rather than an edit: .env is gitignored, and this repo routes
# secret writes to it through scripts (set-operator-token.sh,
# set-identity-shadow-rollout.sh, store-anchor-key.sh). Two hazards it removes:
#
#   * the value never reaches argv — `KEY=... some-command` is readable out of
#     `ps` by other local processes, an interactive `read -rs` is not;
#   * it UPSERTS, so rotating is this same command again rather than a hunt for
#     the right line in a 100-line file.
#
# NOT for scripts/rotate_secrets.py. That registry GENERATES replacement values
# locally (os.urandom), which is correct for secrets this box owns and wrong for
# a third-party credential: it would overwrite a working key with random hex.
# Rotation for this one means re-issuing at https://typesafe.ai, then running
# this script again — as of 2026-09-20 the key in .env is a development key and
# is expected to be rotated before production use.
#
# The endpoint the key authenticates against, and what 401 means, are recorded
# in .env.example next to the placeholder.
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE=".env"
KEY="TYPESAFE_API_KEY"

case "${1:-status}" in
  status|set) ;;
  *) echo "usage: $0 [status|set]" >&2; exit 2 ;;
esac

current() {
  grep -E "^${KEY}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"''
}

if [ "${1:-status}" = "status" ]; then
  value="$(current || true)"
  if [ -z "$value" ]; then
    echo "[typesafe] ${KEY} unset — run: bash scripts/set-typesafe-key.sh set"
  else
    echo "[typesafe] ${KEY} set (${#value} chars, …${value: -4}) — value never printed"
  fi
  exit 0
fi

value="${TYPESAFE_API_KEY:-}"
if [ -z "$value" ]; then
  printf '[typesafe] paste the key (input hidden): ' >&2
  read -rs value
  printf '\n' >&2
fi
[ -n "$value" ] || { echo "[typesafe] no key supplied" >&2; exit 1; }

# Shape check. Narrow on purpose: it keeps the value safe to interpolate into
# sed (no delimiter, backslash or ampersand can appear) and catches the common
# mistake of pasting the wrong clipboard before it reaches .env.
case "$value" in
  *[!A-Za-z0-9_-]*) echo "[typesafe] refusing: key contains characters outside [A-Za-z0-9_-]" >&2; exit 1 ;;
esac
case "$value" in
  apikey_*) ;;
  *) echo "[typesafe] WARNING: no 'apikey_' prefix — is this a TypeSafe key?" >&2 ;;
esac

if grep -qE "^${KEY}=" "$ENV_FILE" 2>/dev/null; then
  sed -i.bak "s|^${KEY}=.*|${KEY}=${value}|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
  action="updated"
else
  {
    printf '\n# TypeSafe AI (Jev / System One decisions) — see scripts/set-typesafe-key.sh\n'
    printf '%s=%s\n' "$KEY" "$value"
  } >> "$ENV_FILE"
  action="added"
fi

# Deliberately NO chmod here. The mode is set in the change that owns it: H4's
# mode sub-claim was closed on 2026-09-20 (JOB-025) with the audit record and
# the wrongness test updated together, and .env is now 0600. Changing the mode
# from a key-storage script is how a live finding gets flipped as a side effect
# instead — it happened once on 2026-09-20 and was reverted, which is why
# "change_file_modes_to_close_a_finding" was a forbidden operation in that job.
# This script preserves whatever mode it finds (`sed -i.bak` rewrites in place).
echo "[typesafe] ${action} ${KEY} (${#value} chars); .env mode untouched at $(stat -f '%Lp' "$ENV_FILE")"
