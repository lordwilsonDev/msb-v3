#!/usr/bin/env bash
set -euo pipefail

# install-hooks.sh — one-time setup for the msb-v3 signature hooks.
#
# Anyone who clones this repo runs this once (or just commits — the hooks
# tell you what's missing). It:
#   1. Points git at the committed hooks/ pack (core.hooksPath), so every
#      checkout/merge records a cryptographically signed pull entry and
#      commits require signing + a Signed-off-by trailer.
#   2. Generates a dedicated SSH signing key (~/.msb-v3/signing_key,
#      passphrase-less) if none exists — used to sign pull records and
#      commits.
#   3. Writes ~/.msb-v3/allowed_signers so the pull ledger can be verified.
#   4. Sets local git config for signing (commit.gpgsign, gpg.format ssh,
#      user.signingkey) only where not already set.
#
# Idempotent and safe. To remove: git config --unset core.hooksPath.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEY="${MSB_PULL_SIGNING_KEY:-$HOME/.msb-v3/signing_key}"
LEDGER="${MSB_PULL_LEDGER:-$HOME/.msb-v3/pull-signatures.log}"
ALLOWED="${MSB_PULL_ALLOWED:-$HOME/.msb-v3/allowed_signers}"
IDENTITY="msb-signing-key"

mkdir -p "$(dirname "$KEY")"

# --- 1. hooks ---------------------------------------------------------------
git config core.hooksPath "$REPO/hooks"
echo "[install-hooks] hooks active (core.hooksPath -> $REPO/hooks)"

# --- 2. signing key -----------------------------------------------------------
if [ ! -f "$KEY" ]; then
  ssh-keygen -t ed25519 -N "" -C "$IDENTITY" -f "$KEY" >/dev/null
  echo "[install-hooks] generated signing key: $KEY"
else
  echo "[install-hooks] signing key present: $KEY"
fi

# --- 3. allowed_signers (for ledger verification) ------------------------------
PUB="$(cut -d' ' -f1,2 < "$KEY.pub")"
if [ -f "$ALLOWED" ] && grep -q "$PUB" "$ALLOWED" 2>/dev/null; then
  echo "[install-hooks] allowed_signers up to date"
else
  printf '%s %s\n' "$IDENTITY" "$PUB" >> "$ALLOWED"
  echo "[install-hooks] wrote $ALLOWED"
fi

# --- 4. git signing config (only where unset — never override) -----------------
git config --get commit.gpgsign >/dev/null 2>&1 || git config commit.gpgsign true
git config --get gpg.format >/dev/null 2>&1 || git config gpg.format ssh
git config --get user.signingkey >/dev/null 2>&1 || git config user.signingkey "$KEY"
git config --get gpg.ssh.allowedSignersFile >/dev/null 2>&1 || git config gpg.ssh.allowedSignersFile "$ALLOWED"
echo "[install-hooks] commit.gpgsign=$(git config --get commit.gpgsign) gpg.format=$(git config --get gpg.format)"

if [ -z "$(git config --get user.name)" ] || [ -z "$(git config --get user.email)" ]; then
  echo "[install-hooks] WARNING: user.name / user.email not set — configure them for commits:"
  echo "  git config user.name  \"Your Name\""
  echo "  git config user.email \"you@example.com\""
fi

# --- source license ------------------------------------------------------------
# Only the OWNER's key (the one committed at config/license-authorized-keys)
# can sign a valid license. On the owner machine this key is the local
# signing key, so self-issue a license and the server gate keeps working.
# On any other machine the local key differs -> no license -> the server
# refuses to start until a license is obtained via request-access.sh.
AUTH_KEYS="$REPO/config/license-authorized-keys"
if [ -f "$AUTH_KEYS" ] && grep -qF "$(cut -d' ' -f1,2 "$KEY.pub")" "$AUTH_KEYS"; then
  owner="$(git config --get user.name 2>/dev/null || echo owner)"
  if bash "$REPO/scripts/issue-license.sh" "$owner" >/dev/null 2>&1; then
    echo "[install-hooks] source license issued for '$owner' (owner key)"
  else
    echo "[install-hooks] WARNING: could not self-issue source license"
  fi
else
  echo "[install-hooks] NOTE: no source license — the server will not start without one."
  echo "  Fork the repo and request a license: bash scripts/request-access.sh"
fi

# --- summary -------------------------------------------------------------------
echo
echo "[install-hooks] done. From now on:"
echo "  - every checkout/merge appends a signed entry to $LEDGER"
echo "  - commits require signing (-S) + a Signed-off-by trailer (git commit -s)"
echo
echo "  To show commits as 'Verified' on GitHub, register the public key as a"
echo "  SIGNING key (Settings -> SSH and GPG keys -> New SSH key, type 'Signing'):"
sed 's/^/    /' "$KEY.pub"
echo
echo "  Verify the pull ledger: bash scripts/verify-pull-signatures.sh"
