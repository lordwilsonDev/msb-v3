#!/usr/bin/env bash
# yubikey-enroll.sh — one-time YubiKey PIV enrollment for MSB's chain anchor.
#
# Generates a P-256 key in PIV slot 9a (default), writes a self-signed
# certificate (required by PKCS#11 Sign), sets a PIN, and stores the PIN for
# the unattended launchd notary/verify jobs.
#
# Usage:
#   bash scripts/yubikey-enroll.sh [SLOT] [PIN]
#     SLOT  PIV slot hex (default: 9a — Authentication; 9c = Signature)
#     PIN   New PIV PIN (default: prompt; must be 6-8 digits)
#
# Requires: a YubiKey 5 (or Security Key with PIV) plugged in, ykman installed
#           (brew install ykman), and yubico-piv-tool for libykcs11
#           (brew install yubico-piv-tool).
#
# The private key is generated ON the YubiKey and never leaves it. The
# certificate is only a carrier for the public key — PKCS#11 Sign refuses to
# sign without one present on the slot.
#
# After enrollment, point MSB at the hardware backend:
#   MSB_CHAIN_ANCHOR_BACKEND=yubikey
#   MSB_YUBIKEY_PIV_SLOT=9a
# (the PIN is read from ~/.yubikey-pin by default)

set -euo pipefail

SLOT="${1:-9a}"
PIN="${2:-}"

echo "=== YubiKey PIV enrollment for MSB chain anchor (slot ${SLOT}) ==="

# 1. Prerequisites
if ! command -v ykman >/dev/null 2>&1; then
  echo "ERROR: ykman not found — install with: brew install ykman" >&2
  exit 1
fi

echo "--- checking YubiKey is present..."
if ! ykman list 2>/dev/null | grep -qi yubikey; then
  echo "ERROR: no YubiKey detected — plug one in and retry" >&2
  exit 1
fi

# 2. Get/set the PIN (default factory PIN is 123456)
if [ -z "$PIN" ]; then
  read -r -s -p "Enter the NEW PIV PIN (6-8 digits): " PIN
  echo
  if [ -z "$PIN" ]; then
    echo "ERROR: no PIN given" >&2
    exit 1
  fi
fi

echo "--- changing PIV PIN (from factory default 123456 if still set)"
# Ignore failure: the PIN may already be changed.
ykman piv access change-pin --pin 123456 --new-pin "$PIN" >/dev/null 2>&1 || true

# 3. Generate P-256 key ON the device (never leaves the YubiKey)
echo "--- generating P-256 key in slot ${SLOT} (on-device)..."
PUBKEY="$(mktemp)"
ykman piv keys generate --algorithm ECCP256 "$SLOT" "$PUBKEY"
echo "    public key written to ${PUBKEY} (can be deleted after use)"

# 4. Self-signed certificate (required by PKCS#11 Sign)
echo "--- generating self-signed certificate..."
ykman piv certificates generate --subject "CN=msb-chain-anchor" "$SLOT" "$PUBKEY"
rm -f "$PUBKEY"

# 5. Store the PIN for the unattended launchd jobs (chmod 600)
echo "--- storing PIN at ~/.yubikey-pin (chmod 600)..."
umask 077
printf '%s\n' "$PIN" > ~/.yubikey-pin
chmod 600 ~/.yubikey-pin

# 6. Verify
echo "--- verifying..."
PUBKEY2="$(mktemp)"
ykman piv keys export "$SLOT" "$PUBKEY2"
echo "    exported public key:"
grep -v -- "-----" "$PUBKEY2" | tr -d '\n' | head -c 120
echo
rm -f "$PUBKEY2"

cat <<EOF

=== Enrollment complete. Point MSB at the hardware backend: ===
  export MSB_CHAIN_ANCHOR_BACKEND=yubikey
  export MSB_YUBIKEY_PIV_SLOT=${SLOT}

(The PIN is read automatically from ~/.yubikey-pin; set MSB_YUBIKEY_PIN to
override, or MSB_YUBIKEY_PKCS11_LIB if libykcs11 lives somewhere else.)

Then verify end-to-end:
  bash scripts/verify_chain_anchor.sh

The private key never left the YubiKey. To use a different slot (e.g. 9c
for Signature instead of 9a), re-run with the slot as the first argument.
EOF
