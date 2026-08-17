#!/usr/bin/env bash
set -euo pipefail

# Build the Secure Enclave helper for the chain anchor (macOS only).
# Compiles scripts/secenclave/secenclave.swift to $HOME/.local/bin/secenclave-tool
# (override with DEST=... or an argument). The Python SecureEnclaveBackend
# resolves the tool via MSB_SECURE_ENCLAVE_TOOL, then ~/.local/bin/secenclave-tool.
#
# Usage:
#   scripts/secenclave/build.sh [<dest>]

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="${1:-${DEST:-$HOME/.local/bin/secenclave-tool}}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "[secenclave] Secure Enclave is macOS-only — nothing to build" >&2
  exit 0
fi

mkdir -p "$(dirname "$DEST")"
swiftc -O -o "$DEST" "$REPO/scripts/secenclave/secenclave.swift"
echo "[secenclave] built $DEST"

# Smoke test: the tool must run and fail-closed with a JSON error (no key yet).
OUT="$("$DEST" public --label __probe__ 2>&1)" || true
case "$OUT" in
  *'"error"'*) echo "[secenclave] smoke OK (fail-closed on missing key)" ;;
  *) echo "[secenclave] WARNING: unexpected probe output: $OUT" >&2 ;;
esac
