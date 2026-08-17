#!/usr/bin/env bash
set -euo pipefail

# Move the chain-anchor Ed25519 seed into the macOS login keychain so it is
# no longer a plaintext string in .env (the current -rw-r--r-- world-readable
# file is readable by any local process). The keychain item is encrypted at
# rest and protected when the machine is locked — the best hardening available
# without an Apple ID (Secure Enclave) or a YubiKey; see
# docs/operations/secure-enclave-anchor.md for the hardware completion path.
#
# After this runs, the runtime resolves the seed via
# MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE (set below) instead of the env/file.

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
SERVICE="${MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE:-msb-chain-anchor-key}"
ACCOUNT="${MSB_CHAIN_ANCHOR_KEYCHAIN_ACCOUNT:-msb-v3}"

set -a
[ -f "$REPO/.env" ] && . "$REPO/.env"
set +a

SEED="${MSB_CHAIN_ANCHOR_KEY:-}"
if [ -z "$SEED" ] && [ -f "$REPO/data/uac/chain_anchor_key" ]; then
  SEED="$(cat "$REPO/data/uac/chain_anchor_key")"
fi
[ -n "$SEED" ] || { echo "[store-anchor-key] no seed found (MSB_CHAIN_ANCHOR_KEY or data/uac/chain_anchor_key)" >&2; exit 1; }

security add-generic-password -a "$ACCOUNT" -s "$SERVICE" -w "$SEED" -U
echo "[store-anchor-key] stored in login keychain ($SERVICE / $ACCOUNT)"

if grep -q "^MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE=" "$REPO/.env"; then
  sed -i.bak "s|^MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE=.*|MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE=$SERVICE|" "$REPO/.env" && rm -f "$REPO/.env.bak"
else
  printf 'MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE=%s\n' "$SERVICE" >> "$REPO/.env"
fi
# Remove the plaintext seed from .env and lock the file down.
sed -i.bak "/^MSB_CHAIN_ANCHOR_KEY=/d" "$REPO/.env" && rm -f "$REPO/.env.bak"
chmod 600 "$REPO/.env"
echo "[store-anchor-key] removed MSB_CHAIN_ANCHOR_KEY from .env and chmod 600"
echo "[store-anchor-key] verify: python -m msb_v3.uac.chain_anchor --verify <audit.db>"
