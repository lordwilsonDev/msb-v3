#!/usr/bin/env bash
set -euo pipefail

# verify-pull-signatures.sh — verify every signed entry in the pull ledger.
#
# Each ledger line is
#   TS|user@host|from..to|SIG:<base64 ssh signature>
# The signature covers the TS|user@host|from..to portion, made with the
# signer's key over the namespace msb-v3-pull. Entries verify against the
# trusted signers in ~/.msb-v3/allowed_signers, which install-hooks.sh seeds
# from the committed config/pull-trusted-keys file — so entries from the
# owner AND any added witness (second signature trust) verify by name.
#
# Each entry is attributed to the trusted signer whose key actually made it
# ("signed by <principal>"), and a coverage report at the end lists every
# trusted signer's entry count — a witness with zero entries is flagged as
# not-yet-active (informational, not an error). Entries that verify under no
# trusted key are INVALID and the script exits non-zero.
#
# Overrides: MSB_PULL_LEDGER, MSB_PULL_ALLOWED.

LEDGER="${MSB_PULL_LEDGER:-$HOME/.msb-v3/pull-signatures.log}"
ALLOWED="${MSB_PULL_ALLOWED:-$HOME/.msb-v3/allowed_signers}"
NAMESPACE="msb-v3-pull"

[ -f "$LEDGER" ] || { echo "no pull ledger yet at $LEDGER — no pulls/checkouts recorded on this machine (not an error; the first pull seeds it)"; exit 0; }
[ -f "$ALLOWED" ] || { echo "no allowed_signers at $ALLOWED — run scripts/install-hooks.sh"; exit 1; }

tmp="$(mktemp -d /tmp/pull-sig-verify-XXXXXX)"
trap 'rm -rf "$tmp"' EXIT

# --- index trusted signers: one restricted allowed-file per label -----------
# A principal may map to multiple keys (key rotation leaves the old key
# trusted under the same label), so per-label files APPEND; verification
# with -I <label> tries every key line for that principal.
labels=""
nkeys=0
while read -r princ kt key; do
  [ -n "$princ" ] || continue
  case "$princ" in \#*) continue;; esac
  [ -n "$kt" ] && [ -n "$key" ] || continue
  printf '%s %s %s\n' "$princ" "$kt" "$key" >> "$tmp/allow-$princ"
  case " $labels " in *" $princ "*) ;; *) labels="$labels $princ"; nkeys=$((nkeys + 1));; esac
done < "$ALLOWED"

if [ "$nkeys" -eq 0 ]; then
  echo "allowed_signers at $ALLOWED contains no trusted keys — add witnesses via config/pull-trusted-keys or install-hooks.sh"
  exit 1
fi

# --- verify each entry, attributing to the signing witness ------------------
total=0
valid=0
invalid=0
echo "=== pull signature ledger: $LEDGER ==="
echo "=== trusted signers:$labels ==="
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
  signer=""
  for lbl in $labels; do
    if cat "$tmp/msg" | ssh-keygen -Y verify -f "$tmp/allow-$lbl" -I "$lbl" \
        -n "$NAMESPACE" -s "$tmp/sig" >/dev/null 2>&1; then
      signer="$lbl"
      break
    fi
  done
  if [ -n "$signer" ]; then
    valid=$((valid + 1))
    echo "  ok (signed by $signer): $data" | tee -a "$tmp/verdicts"
  else
    invalid=$((invalid + 1))
    echo "  INVALID (no trusted witness): $data"
  fi
done < "$LEDGER"

echo
echo "=== $total entries, $valid valid, $invalid invalid ==="

# --- witness coverage --------------------------------------------------------
echo "=== witness coverage ==="
active=0
for lbl in $labels; do
  c=$(grep -c "signed by $lbl):" "$tmp/verdicts" 2>/dev/null || true)
  if [ "${c:-0}" -gt 0 ]; then
    echo "  $lbl: $c entr$( [ "$c" = 1 ] && echo y || echo ies) signed"
    active=$((active + 1))
  else
    echo "  $lbl: 0 entries — trusted witness not yet active (informational)"
  fi
done
echo "=== $active of $nkeys trusted witness(es) have signed the trail ==="

[ "$invalid" -eq 0 ]
