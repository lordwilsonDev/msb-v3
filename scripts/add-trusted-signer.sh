#!/usr/bin/env bash
set -euo pipefail

# add-trusted-signer.sh — add a second witness to the pull-signature trust.
#
# The repo's pull-signature trail verifies against config/pull-trusted-keys
# (seeded into ~/.msb-v3/allowed_signers by install-hooks.sh). This helper
# appends a new signer's public key to that committed file and re-seeds the
# local allowed_signers, so entries signed by the new witness verify and the
# audit reports them by name.
#
# Usage:
#   bash scripts/add-trusted-signer.sh <pubkey-file> [label]
#   bash scripts/add-trusted-signer.sh "<label> <keytype> <base64>"   (inline)
#
# Label defaults to the pubkey file's basename minus .pub. Only the owner
# should run this (they are the one with push access to the trusted file).
#
# Overrides (testing): MSB_TRUSTED_KEYS (committed file), MSB_PULL_ALLOWED.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRUSTED="${MSB_TRUSTED_KEYS:-$REPO/config/pull-trusted-keys}"
ALLOWED="${MSB_PULL_ALLOWED:-$HOME/.msb-v3/allowed_signers}"

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  echo "usage: add-trusted-signer.sh <pubkey-file> [label]  |  add-trusted-signer.sh \"<label> <keytype> <base64>\"" >&2
  exit 2
fi

if [ -f "$1" ]; then
  # pubkey files are "<keytype> <base64> [comment]" — key material is fields 1-2
  read -r kt key _comment < <(head -1 "$1")
  label="${2:-$(basename "$1" .pub)}"
  [ -n "$kt" ] && [ -n "$key" ] || { echo "bad pubkey file: $1" >&2; exit 2; }
  line="$label $kt $key"
else
  line="$1"
  label="$(echo "$line" | awk '{print $1}')"
  [ $# -lt 2 ] || label="$2"
fi

# validate: <label> <keytype> <b64> (no whitespace inside fields)
read -r lbl kt key <<<"$line"
[ -n "$lbl" ] && [ -n "$kt" ] && [ -n "$key" ] || { echo "bad key line: $line" >&2; exit 2; }
case "$lbl" in *[!A-Za-z0-9._-]*) echo "label must be [A-Za-z0-9._-], got: $lbl" >&2; exit 2;; esac
case "$key" in *[!A-Za-z0-9+/=]*) echo "key material has invalid characters" >&2; exit 2;; esac

if [ -f "$TRUSTED" ] && grep -qF "$kt $key" "$TRUSTED"; then
  echo "[add-trusted-signer] key already trusted ($lbl) — nothing to do"
else
  printf '%s %s %s\n' "$lbl" "$kt" "$key" >> "$TRUSTED"
  echo "[add-trusted-signer] appended to $TRUSTED"
fi

# re-seed allowed_signers from the trusted file (merge, never drop existing)
mkdir -p "$(dirname "$ALLOWED")"
tmp="$(mktemp /tmp/trusted-seed-XXXXXX)"
trap 'rm -f "$tmp"' EXIT
[ -f "$ALLOWED" ] && cp "$ALLOWED" "$tmp" || : > "$tmp"
while read -r p k kk; do
  [ -n "$p" ] || continue
  case "$p" in \#*) continue;; esac
  grep -qF "$k $kk" "$tmp" 2>/dev/null || printf '%s %s %s\n' "$p" "$k" "$kk" >> "$tmp"
done < "$TRUSTED"
cp "$tmp" "$ALLOWED"
echo "[add-trusted-signer] allowed_signers re-seeded -> $ALLOWED"
echo "  Now ask the new witness to sign a pull (or sign one for them); verify: bash scripts/verify-pull-signatures.sh"
