#!/usr/bin/env bash
set -euo pipefail

# verify-pull-signatures.sh — verify every signed entry in the pull ledger.
#
# Each ledger line is
#   TS|user@host|from..to|SIG:<base64 ssh signature>
# The signature covers the TS|user@host|from..to portion, made with the
# signer's key in ~/.msb-v3 (identity msb-signing-key) over the namespace
# msb-v3-pull. Entries whose signature does not verify are reported and the
# script exits non-zero. Entries from other people's keys verify only if
# their public keys are in ~/.msb-v3/allowed_signers.
#
# Overrides: MSB_PULL_LEDGER, MSB_PULL_SIGNING_KEY, MSB_PULL_ALLOWED.

LEDGER="${MSB_PULL_LEDGER:-$HOME/.msb-v3/pull-signatures.log}"
ALLOWED="${MSB_PULL_ALLOWED:-$HOME/.msb-v3/allowed_signers}"
IDENTITY="msb-signing-key"
NAMESPACE="msb-v3-pull"

[ -f "$LEDGER" ] || { echo "no pull ledger yet at $LEDGER — no pulls/checkouts recorded on this machine (not an error; the first pull seeds it)"; exit 0; }
[ -f "$ALLOWED" ] || { echo "no allowed_signers at $ALLOWED — run scripts/install-hooks.sh"; exit 1; }

tmp="$(mktemp -d /tmp/pull-sig-verify-XXXXXX)"
trap 'rm -rf "$tmp"' EXIT

total=0
valid=0
invalid=0
echo "=== pull signature ledger: $LEDGER ==="
while IFS= read -r line; do
  [ -n "$line" ] || continue
  total=$((total + 1))
  data="${line%%|SIG:*}"
  sig_b64="${line##*|SIG:}"
  printf '%s' "$sig_b64" | base64 -d > "$tmp/sig" 2>/dev/null || {
    echo "  INVALID (undecodable): $data"; invalid=$((invalid + 1)); continue
  }
  printf '%s' "$data" > "$tmp/msg"
  # NB: feed the message via STDIN, never as a positional file arg — on
  # macOS's ssh-keygen (OpenSSH 10.0p2/LibreSSL) a file-arg message makes
  # verification fail spuriously, while stdin verifies cleanly (git uses
  # the same stdin pattern).
  if cat "$tmp/msg" | ssh-keygen -Y verify -f "$ALLOWED" -I "$IDENTITY" -n "$NAMESPACE" -s "$tmp/sig" >/dev/null 2>&1; then
    valid=$((valid + 1))
    echo "  ok: $data"
  else
    invalid=$((invalid + 1))
    echo "  INVALID: $data"
  fi
done < "$LEDGER"

echo
echo "=== $total entries, $valid valid, $invalid invalid ==="
[ "$invalid" -eq 0 ]
