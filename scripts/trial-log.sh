#!/usr/bin/env bash
# M6 trial logger — run one real task through the live governed path and
# append a ledger entry with the run evidence (run_id, verdict, hash,
# duration) captured automatically. Usage:
#
#   bash scripts/trial-log.sh "<request>" "<output dir or 'readonly'>" ["<baseline minutes>"]
#
# The write lands at {output_dir}/{slug}.md (the slice derives the filename
# from the goal — the directory is what you control).
#
# Examples:
#   bash scripts/trial-log.sh "Search the vault for recent decisions about caching and write a note" /tmp/msb-trial 15
#   bash scripts/trial-log.sh "Search the vault for what the sovereign stack is" readonly 10
set -euo pipefail
cd "$(dirname "$0")/.."

REQUEST="${1:?usage: trial-log.sh \"<request>\" \"<output|readonly>\" [baseline-minutes]}"
OUT="${2:?usage: trial-log.sh \"<request>\" \"<output|readonly>\" [baseline-minutes]}"
BASELINE="${3:-}"
LEDGER="docs/blueprints/convergence-to-12/operating-ledger-entries.md"

TOKEN=$(grep "^MSB_OPERATOR_TOKEN=" .env | cut -d= -f2- | tr -d '"' | tr -d "'")
[ -n "$TOKEN" ] || { echo "MSB_OPERATOR_TOKEN not found in .env" >&2; exit 1; }

# Decide approve flag + output dir: writes need operator pre-auth and a
# landing directory (the write lands at {output_dir}/{slug}.md — the slice
# derives the filename from the goal; the directory is what we control).
if [ "$OUT" = "readonly" ]; then
  APPROVE=0
  OUTPUT_DIR=""
else
  APPROVE=1
  OUTPUT_DIR="$OUT"
fi

PAYLOAD=$(python3 - "$REQUEST" "$APPROVE" "$OUTPUT_DIR" <<'PYEOF'
import json, sys
payload = {"request": sys.argv[1], "approve": bool(int(sys.argv[2]))}
if sys.argv[3]:
    payload["output_dir"] = sys.argv[3]
print(json.dumps(payload))
PYEOF
)

echo "== running task (approve=$APPROVE) =="
START=$(date +%s)
RESP=$(curl -s -X POST http://127.0.0.1:8766/agent/handle \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "$PAYLOAD" || true)
END=$(date +%s)
DURATION=$((END - START))

# Parse the response (handle may return 200 {ok:true,...} or a 4xx/5xx
# {"detail": {...}} envelope — both carry the run record).
python3 - "$RESP" "$REQUEST" "$OUT" "$BASELINE" "$DURATION" "$LEDGER" <<'PYEOF'
import json, sys, datetime, re

resp, request, out, baseline, duration, ledger = (
    sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), sys.argv[6],
)
try:
    data = json.loads(resp)
except json.JSONDecodeError:
    data = {}

if "detail" in data and isinstance(data["detail"], dict):
    data = data["detail"]  # error envelope

ok = bool(data.get("ok", False))
verdict = data.get("verdict") or ("PASS" if ok else "FAIL")
run_id = data.get("run_id") or data.get("trace", {}).get("run_id") or "?"
hash_ = data.get("deterministic_hash") or "?"
error = data.get("error") or ("" if ok else "see response")

# Auto-number: next Entry NNN after the highest existing one.
text = open(ledger, encoding="utf-8").read()
nums = [int(m) for m in re.findall(r"^## Entry (\d+)", text, re.MULTILINE)]
next_num = (max(nums) + 1) if nums else 1

entry = f"""
## Entry {next_num:03d} — {datetime.date.today().isoformat()} · Trial task

**Task:** {request}

**Output:** {out} · **Baseline:** {baseline or "n/a"} min

**MSB result:** **{verdict}.** run `{run_id}`, deterministic hash `{hash_}`,
~{duration}s{(" — " + error) if error else "."}

**Intervention:** (approve / fix / retry / bypass — fill in)
**Evidence quality:** (was the audit/evidence record consulted or useful?)
**Value:** (time saved / error prevented / quality improvement)
"""

with open(ledger, "a", encoding="utf-8") as fh:
    fh.write(entry)
print(f"logged: {verdict} run={run_id} hash={hash_} {duration}s -> {ledger} (Entry {next_num:03d})")
PYEOF
