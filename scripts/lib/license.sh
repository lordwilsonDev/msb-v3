#!/usr/bin/env bash
# Shared helpers for the msb-v3 source-license gate. Sourced by
# scripts/verify-license.sh, scripts/issue-license.sh, scripts/run.sh.
#
# A source license is a single line:
#   holder=<name>|granted=<YYYY-MM-DD>|scope=<full|demo>|repo=<owner/name>
#   |SIG:<base64 ssh signature>
# signed by the OWNER's key over the namespace msb-v3-source-license. The
# public key authorized to issue licenses is committed at
# config/license-authorized-keys. The server (scripts/run.sh) refuses to
# start without a valid license, so an anonymous pull is inert code — to
# use the repo you must fork it and obtain a license from the owner.

# shellcheck disable=SC2155
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$LIB_DIR/../.." && pwd)"

LICENSE_FILE="${MSB_LICENSE_FILE:-$HOME/.msb-v3/source-license}"
LICENSE_KEY="${MSB_LICENSE_KEY:-$HOME/.msb-v3/signing_key}"
LICENSE_AUTHORIZED="${MSB_LICENSE_AUTHORIZED:-$REPO/config/license-authorized-keys}"
LICENSE_NAMESPACE="msb-v3-source-license"
LICENSE_IDENTITY="msb-signing-key"

# Sign a license line with the local key (stdin — macOS ssh-keygen quirk).
license_sign() { # data-line -> echoes base64 armor
  local armored
  armored="$(printf '%s' "$1" | ssh-keygen -Y sign -f "$LICENSE_KEY" -n "$LICENSE_NAMESPACE" 2>/dev/null)" || return 1
  printf '%s' "$armored" | base64 | tr -d '\n'
}

# Verify a license file (or line) against the committed authorized key.
# Prints one status word on stdout; returns 0 valid, 1 invalid, 2 missing.
license_status() { # [license-file]
  local f="${1:-$LICENSE_FILE}" line data sigb64
  [ -f "$f" ] || { echo "missing"; return 2; }
  [ -f "$LICENSE_AUTHORIZED" ] || { echo "no-authorized-key"; return 1; }
  line="$(head -1 "$f")"
  data="${line%%|SIG:*}"
  sigb64="${line##*|SIG:}"
  [ -n "$data" ] && [ "$data" != "$line" ] || { echo "malformed"; return 1; }
  local tmp
  tmp="$(mktemp -d /tmp/license-verify-XXXXXX)"
  trap 'rm -rf "$tmp"' RETURN
  printf '%s' "$sigb64" | base64 -d > "$tmp/sig" 2>/dev/null || { echo "malformed"; return 1; }
  printf '%s' "$data" > "$tmp/msg"
  # NB: stdin for the message (Apple's ssh-keygen rejects a file-arg message).
  if cat "$tmp/msg" | ssh-keygen -Y verify -f "$LICENSE_AUTHORIZED" \
      -I "$LICENSE_IDENTITY" -n "$LICENSE_NAMESPACE" -s "$tmp/sig" >/dev/null 2>&1; then
    echo "valid"
    return 0
  fi
  echo "invalid"
  return 1
}
