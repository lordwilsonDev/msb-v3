#!/usr/bin/env bash
set -euo pipefail

# qdrant-sweep -- one-command cleanup of test-named Qdrant collections.
#
# Deletes collections whose tenant name contains "test" (case-insensitive):
#   tenant_live_test_*, tenant_embed-test, tenant_test-client,
#   tenant_verify-test, tenant_verify-test-2, tenant_wilson-vault-test, ...
# Production tenants (e.g. tenant_wilson-vault) are never matched by that
# classifier, AND are additionally protected by an explicit keep-list so a
# classifier change can never sweep them.
#
# An audit inventory of the sweep is written to artifacts/qdrant-sweep-<ts>.json.
#
# Usage:
#   scripts/qdrant-sweep.sh             # delete test-named collections
#   scripts/qdrant-sweep.sh --dry-run   # show what would be deleted, delete nothing

PORT="${QDRANT_PORT:-6333}"
BASE="http://127.0.0.1:$PORT"
REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
# Explicit keep-list (space-separated): production tenants must never be
# swept even if a name ever matches the test classifier.
PROTECTED="${QDRANT_SWEEP_KEEP:-tenant_wilson-vault}"

DRY=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY=1
fi

if ! curl -sf -m 3 "$BASE/healthz" >/dev/null; then
  echo "[qdrant-sweep] ERROR: Qdrant not reachable at $BASE -- start it first (make qdrant-start)" >&2
  exit 1
fi

mkdir -p "$REPO/artifacts"
TS=$(date +%Y%m%dT%H%M%SZ)

# Classify: fetch collections, mark test-named ones for sweep (never the
# protected list), write the audit inventory, print sweep names on stdout.
SWEEP_LIST=$(QDRANT_BASE="$BASE" QDRANT_SWEEP_KEEP="$PROTECTED" REPO="$REPO" \
  "${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}" - "$TS" <<'PYEOF'
import json, os, re, sys, urllib.request, datetime

base = os.environ["QDRANT_BASE"]
protected = set(os.environ.get("QDRANT_SWEEP_KEEP", "").split())
ts = sys.argv[1]
repo = os.environ["REPO"]

# Token-boundary match (not substring): "test" must sit at a name/underscore/
# hyphen boundary, so it catches live_test_, -test, test- but never a real
# tenant whose name merely CONTAINS "test" (e.g. tenant_attest).
_TEST_TOKEN = re.compile(r"(?:^|[-_])test(?:[-_]|$)")
names = [c["name"] for c in json.load(
    urllib.request.urlopen(base + "/collections"))["result"]["collections"]]
sweep = sorted(n for n in names if _TEST_TOKEN.search(n.lower()) and n not in protected)
keep = sorted(n for n in names if n not in sweep)

inventory = {
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "swept": sweep,
    "kept": keep,
    "protected": sorted(protected),
}
with open(f"{repo}/artifacts/qdrant-sweep-{ts}.json", "w") as fh:
    fh.write(json.dumps(inventory, indent=2) + "\n")
print("\n".join(sweep))
PYEOF
)

if [ -z "$SWEEP_LIST" ]; then
  echo "[qdrant-sweep] nothing to sweep (no test-named collections)"
  exit 0
fi

echo "[qdrant-sweep] found $(echo "$SWEEP_LIST" | grep -c .) test-named collection(s):"
echo "$SWEEP_LIST" | sed 's/^/  - /'
if [ "$DRY" = 1 ]; then
  echo "[qdrant-sweep] dry-run -- nothing deleted (audit: artifacts/qdrant-sweep-$TS.json)"
  exit 0
fi

ok=0; fail=0
while IFS= read -r c; do
  [ -z "$c" ] && continue
  code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$BASE/collections/$c")
  if [ "$code" = "200" ]; then
    ok=$((ok + 1))
    echo "  deleted $c"
  else
    fail=$((fail + 1))
    echo "  FAIL $c (HTTP $code)" >&2
  fi
done <<< "$SWEEP_LIST"
echo "[qdrant-sweep] deleted=$ok failed=$fail (audit: artifacts/qdrant-sweep-$TS.json)"
[ "$fail" = 0 ]
