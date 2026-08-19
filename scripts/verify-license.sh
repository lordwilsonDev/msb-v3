#!/usr/bin/env bash
set -euo pipefail

# verify-license.sh — check a source license against the committed
# authorized key (config/license-authorized-keys). Exit 0 = valid and the
# holder/scope/granted are printed; 1 = invalid/tampered; 2 = missing.
#
# Usage: scripts/verify-license.sh [license-file]
#   (default: ~/.msb-v3/source-license)
#
# Overrides: MSB_LICENSE_FILE, MSB_LICENSE_AUTHORIZED

LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/license.sh"
# shellcheck source=lib/license.sh
. "$LIB"

f="${1:-$LICENSE_FILE}"
set +e
status="$(license_status "$f")"
rc=$?
set -e
case "$status" in
  valid)
    line="$(head -1 "$f")"
    data="${line%%|SIG:*}"
    echo "VALID — ${data//|/  }"
    ;;
  missing) echo "no license at $f (run scripts/install-hooks.sh on the owner machine, or scripts/request-access.sh)" ;;
  *) echo "license $status: $f" ;;
esac
exit "$rc"
