#!/usr/bin/env bash
set -euo pipefail

# issue-license.sh — owner tool: issue an SSH-signed source license.
#
# The license is signed with ~/.msb-v3/signing_key (the key whose public
# half is committed at config/license-authorized-keys) over the namespace
# msb-v3-source-license. Only licenses signed by that key are accepted by
# the server gate and verify-license.sh — a holder cannot self-issue.
#
# Usage: scripts/issue-license.sh <holder> [scope] [outfile]
#   holder   GitHub username or email of the recipient (required)
#   scope    full (default) | demo
#   outfile  where to write the license (default ~/.msb-v3/source-license)
#
# Overrides: MSB_LICENSE_KEY, MSB_LICENSE_FILE (default outfile)

LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/license.sh"
# shellcheck source=lib/license.sh
. "$LIB"

holder="${1:-}"
[ -n "$holder" ] || { echo "usage: $0 <holder> [scope] [outfile]" >&2; exit 2; }
scope="${2:-full}"
case "$scope" in full|demo) ;; *) echo "scope must be full|demo" >&2; exit 2 ;; esac
out="${3:-${MSB_LICENSE_FILE:-$HOME/.msb-v3/source-license}}"

[ -f "$LICENSE_KEY" ] || {
  echo "ERROR: no signing key at $LICENSE_KEY — run scripts/install-hooks.sh first" >&2
  exit 1
}

repo="$(git -C "$REPO" remote get-url origin 2>/dev/null | sed -E 's#.*github.com[:/]##; s#\.git$##' || echo lordwilsonDev/msb-v3)"
data="holder=$holder|granted=$(date +%F)|scope=$scope|repo=$repo"
sig="$(license_sign "$data")" || { echo "ERROR: signing failed" >&2; exit 1; }
mkdir -p "$(dirname "$out")"
printf '%s|SIG:%s\n' "$data" "$sig" > "$out"
echo "issued license for '$holder' (scope=$scope) -> $out"
echo "verify: bash scripts/verify-license.sh $out"
