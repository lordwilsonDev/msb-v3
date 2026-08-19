#!/usr/bin/env bash
# Shared helpers for the msb-v3 signature hooks. Sourced by hooks that
# record pull events. Never fails the parent git operation — recording is
# best-effort by design (an audit trail must not break a checkout).

# Ledger: one line per pull/checkout, each cryptographically signed.
#   TS|user@host|from..to|SIG:<base64 ssh signature>
LEDGER="${MSB_PULL_LEDGER:-$HOME/.msb-v3/pull-signatures.log}"
KEY="${MSB_PULL_SIGNING_KEY:-$HOME/.msb-v3/signing_key}"
NAMESPACE="msb-v3-pull"

record_pull() { # from to
  local from="$1" to="$2" ts user host line armored b64
  mkdir -p "$(dirname "$LEDGER")"
  [ -f "$KEY" ] || {
    echo "[msb-signature] WARN: no signing key at $KEY — run scripts/install-hooks.sh to record signed pulls" >&2
    return 0
  }
  ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  user="$(id -un)"
  host="$(hostname -s)"
  line="$ts|$user@$host|$from..$to"
  armored="$(printf '%s' "$line" | ssh-keygen -Y sign -f "$KEY" -n "$NAMESPACE" 2>/dev/null)" || {
    echo "[msb-signature] WARN: signing failed for pull record" >&2
    return 0
  }
  b64="$(printf '%s' "$armored" | base64 | tr -d '\n')"
  printf '%s|SIG:%s\n' "$line" "$b64" >> "$LEDGER"
}
