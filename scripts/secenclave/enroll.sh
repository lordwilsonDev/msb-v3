#!/usr/bin/env bash
set -euo pipefail

# One-time Secure Enclave enrollment for the chain anchor (macOS).
#
# macOS requires a provisioning-profile-signed binary to persist a Secure
# Enclave key (keychain-access-groups entitlement; unsigned/ad-hoc binaries
# fail with -34018 — see docs/operations/secure-enclave-anchor.md). This
# script:
#   1. waits for / finds your Apple Development identity (sign into Xcode
#      first: Xcode -> Settings -> Accounts, free Apple ID is enough),
#   2. builds the profile-minting KeychainTool app with automatic signing
#      (Xcode generates + embeds the profile),
#   3. wraps secenclave-tool in an .app carrying that profile and codesigns
#      it with the same identity + the Keychain Sharing entitlement,
#   4. enrolls the P-256 key in the Secure Enclave (label msb-chain-anchor).
#
# Usage:  scripts/secenclave/enroll.sh

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LABEL="${MSB_SECURE_ENCLAVE_KEY_LABEL:-msb-chain-anchor}"
DEST="${MSB_SECURE_ENCLAVE_TOOL:-$HOME/.local/bin/secenclave-tool}"
APP="$HOME/.local/share/secenclave-tool.app"
PROJ="$REPO/scripts/secenclave/KeychainTool"

log() { echo "[enroll] $*"; }
die() { echo "[enroll] ERROR: $*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "Secure Enclave is macOS-only"

# 1. Apple Development identity -------------------------------------------------
IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null | grep "Apple Development" | head -1 || true)"
if [ -z "$IDENTITY" ]; then
  die "no Apple Development identity found. Sign into Xcode first: open Xcode -> Settings -> Accounts, add your (free) Apple ID, wait for the 'Apple Development' certificate, then re-run this script."
fi
CERT_PEM="$(security find-certificate -c "Apple Development" -p 2>/dev/null | head -60)"
TEAM="$(printf '%s' "$CERT_PEM" | openssl x509 -noout -subject 2>/dev/null | sed -n 's/.*OU=\([A-Z0-9]\{10\}\).*/\1/p')"
[ -n "$TEAM" ] || die "could not determine your Team ID from the Apple Development certificate"
IDENTITY_NAME="$(printf '%s' "$IDENTITY" | sed -n 's/^ *[0-9A-F]* *"\([^"]*\)".*/\1/p')"
log "identity: $IDENTITY_NAME  team: $TEAM"

# 2. Build the profile-minting app ----------------------------------------------
log "building KeychainTool (generates the provisioning profile)..."
mkdir -p "$(dirname "$DEST")"
xcodebuild -project "$PROJ/KeychainTool.xcodeproj" -target KeychainTool \
  -configuration Release -derivedDataPath /tmp/keychaintool-ddata \
  DEVELOPMENT_TEAM="$TEAM" CODE_SIGN_IDENTITY="Apple Development" \
  -allowProvisioningUpdates -allowProvisioningDeviceRegistration build >/tmp/keychaintool-build.log 2>&1 \
  || { tail -30 /tmp/keychaintool-build.log; die "xcodebuild failed (see log above)"; }
PROFILE="$(find /tmp/keychaintool-ddata -name embedded.mobileprovision -path "*KeychainTool.app*" | head -1)"
[ -n "$PROFILE" ] || die "build succeeded but no embedded.mobileprovision was produced"
log "profile: $PROFILE"

# 3. Wrap + codesign secenclave-tool --------------------------------------------
log "wrapping secenclave-tool in a profile-bearing app bundle..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$DEST" "$APP/Contents/MacOS/secenclave-tool"
cp "$PROFILE" "$APP/Contents/embedded.mobileprovision"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>secenclave-tool</string>
  <key>CFBundleIdentifier</key><string>com.blackswanlabz.msb.secenclave</string>
  <key>CFBundleName</key><string>secenclave-tool</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
</dict>
</plist>
EOF
codesign --force --sign "Apple Development" \
  --entitlements "$PROJ/KeychainTool/KeychainTool.entitlements" \
  --identifier com.blackswanlabz.msb.secenclave \
  "$APP"
codesign --verify --verbose=2 "$APP" || die "codesign verification failed"

# Point the backend at the wrapped binary going forward.
if ! grep -q "^MSB_SECURE_ENCLAVE_TOOL=" "$REPO/.env" 2>/dev/null; then
  printf 'MSB_SECURE_ENCLAVE_TOOL=%s/Contents/MacOS/secenclave-tool\n' "$APP" >> "$REPO/.env"
fi
log "signed tool: $APP/Contents/MacOS/secenclave-tool"

# 4. Enroll the key --------------------------------------------------------------
TOOL="$APP/Contents/MacOS/secenclave-tool"
OUT="$("$TOOL" create --label "$LABEL" 2>&1)" || die "enrollment failed: $OUT"
log "key enrolled: $OUT"
log "NEXT: verify with: $TOOL public --label $LABEL"
log "then rotate the anchor + notary and set MSB_CHAIN_ANCHOR_BACKEND=secure-enclave (see docs/operations/secure-enclave-anchor.md)"
