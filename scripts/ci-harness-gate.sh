#!/usr/bin/env bash
set -uo pipefail

# ci-harness-gate -- CI-consumer gate for the harness-evidence report.
#
# Reads a harness-evidence-report/v1 JSON artifact (written by
# scripts/harness-evidence.sh via HARNESS_REPORT_FILE=...) and blocks the
# build unless the gate verdict is PASS. The producer computes the human
# verdict and the report in one pass, so this consumer can trust
# .gate.verdict without re-evaluating evidence.
#
# Usage:
#   HARNESS_REPORT_FILE=report.json bash scripts/harness-evidence.sh
#   bash scripts/ci-harness-gate.sh report.json
#   make webcheck-all STAGES=endpoints,harness   # producer + gate in one stage
#
# Exit: 0 = gate PASS; 1 = gate BLOCK (verdict not PASS); 2 = env/parse error.
#
# Env:
#   CI_HARNESS_REPORT        report path used when arg 1 is omitted
#                            (default harness-evidence-report.json)
#   CI_HARNESS_FORCE_PYTHON  set to 1 to use the python fallback even when
#                            jq is installed (e.g. testing, or a runner where
#                            the jq behavior is not trusted)
#   CI_HARNESS_PY            python binary for the fallback (default python3)

REPORT="${1:-${CI_HARNESS_REPORT:-harness-evidence-report.json}}"
PY="${CI_HARNESS_PY:-python3}"

if [ ! -f "$REPORT" ]; then
  echo "[ci-harness-gate] ERROR: report not found: $REPORT (pass path as arg 1 or set CI_HARNESS_REPORT)" >&2
  exit 2
fi

# --- python fallback (stdlib only; used when jq is unavailable) ---
if [ "${CI_HARNESS_FORCE_PYTHON:-0}" = "1" ] || ! command -v jq >/dev/null 2>&1; then
  exec "$PY" - "$REPORT" <<'PYEOF'
import json
import sys

path = sys.argv[1]
try:
    with open(path) as fh:
        d = json.load(fh)
except (OSError, ValueError) as e:
    print(f"[ci-harness-gate] ERROR: cannot read {path} as JSON ({type(e).__name__}: {e})", file=sys.stderr)
    sys.exit(2)

if not isinstance(d, dict):
    print(f"[ci-harness-gate] ERROR: {path} is not a JSON object", file=sys.stderr)
    sys.exit(2)

if d.get("schema") != "harness-evidence-report/v1":
    print(f"[ci-harness-gate] ERROR: {path} is not a harness-evidence-report/v1 artifact (schema={d.get('schema')!r})", file=sys.stderr)
    sys.exit(2)

verdict = d.get("gate", {}).get("verdict", "MISSING")
if verdict == "PASS":
    print(f"[ci-harness-gate] PASS: {path}")
    sys.exit(0)

print(f"[ci-harness-gate] BLOCK: gate verdict is '{verdict}' (expected PASS)", file=sys.stderr)
for exp in d.get("gate", {}).get("experiments", []):
    if exp.get("status") != "PASS":
        print(f"  - {exp.get('message') or exp.get('experiment')}", file=sys.stderr)
sys.exit(1)
PYEOF
fi

# --- jq path ---
if ! jq -e 'type == "object"' "$REPORT" >/dev/null 2>&1; then
  echo "[ci-harness-gate] ERROR: $REPORT is not a valid JSON object" >&2
  exit 2
fi
if ! jq -e '.schema == "harness-evidence-report/v1"' "$REPORT" >/dev/null 2>&1; then
  echo "[ci-harness-gate] ERROR: $REPORT is not a harness-evidence-report/v1 artifact" >&2
  exit 2
fi

verdict=$(jq -r '.gate.verdict // "MISSING"' "$REPORT")
if [ "$verdict" = "PASS" ]; then
  echo "[ci-harness-gate] PASS: $REPORT"
  exit 0
fi

echo "[ci-harness-gate] BLOCK: gate verdict is '$verdict' (expected PASS)" >&2
jq -r '(.gate.experiments // [])[] | select(.status != "PASS") | "  - " + (.message // .experiment)' "$REPORT" >&2
exit 1
