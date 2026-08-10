#!/usr/bin/env bash
# Demo: drive the 3-node contract dag (scripts/fixtures/demo_dag_3node.json)
# to completion under the task-contract executor — one command.
#
#   node a  scaffold the retrieval module   -> VERIFIED     (predicates pass)
#   node b  add the answer endpoint         -> VERIFIED     (gated on a:VERIFIED)
#   node c  ship the package                -> ROLLED_BACK  (exit 0 but predicates fail;
#                                                            file_delete rollback confirmed)
#
# The dag file is the stateful store: each `--execute --write-back` call
# advances exactly ONE READY node and persists its status, so three calls
# drive the whole precondition chain (spec §9 pinned granularity).
#
# Usage: scripts/demo_execute_dag.sh [--py /path/to/python]
# Exit:  0 = demo completed as designed (c's rollback IS the intended outcome).
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DAG="$ROOT/scripts/fixtures/demo_dag_3node.json"
PY="${PY:-python3}"

WORK="$(mktemp -d /tmp/task-exec-demo-XXXXXX)"
OUT="$WORK/out"
LEDGER="$WORK/ledger"
mkdir -p "$OUT"
cp "$DAG" "$WORK/dag.json"

echo "== task-contract demo: 3-node dag under the executor =="
echo "   dag:    $DAG (worked on a scratch copy)"
echo "   output: $OUT"
echo "   ledger: $LEDGER"
echo

run() {
  echo "--- advance_dag (selects exactly one READY node) ---"
  "$PY" "$ROOT/scripts/execute_task_contract.py" \
    --execute "$WORK/dag.json" --output-root "$OUT" --ledger-dir "$LEDGER" \
    --goal "demo: build + ship a search service" --write-back
  echo
}

run
run
run

echo "== dag after the run =="
"$PY" - "$WORK/dag.json" <<'PYEOF'
import json, sys
dag = json.load(open(sys.argv[1]))
for e in dag:
    print(f"   {e['task_id']}: {e['status']}")
PYEOF
echo

echo "== ledger =="
"$PY" - "$LEDGER" <<'PYEOF'
import json, pathlib, sys
ledger = pathlib.Path(sys.argv[1])

reg = json.loads((ledger / "claims.json").read_text(encoding="utf-8"))
print("--- claims ---")
for c in reg["claims"]:
    print(f"   {c['claim_id']} [{c['verdict']}] negative={len(c['negative_evidence'])}")

print("--- TASK_FAILED events ---")
ev = ledger / "records" / "task_events.jsonl"
if ev.exists():
    for line in ev.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        print(f"   {e['event']} task={e['task_id']} decision={e['decision']} ts={e['ts']}")
else:
    print("   (none)")

print("--- evidence files ---")
for f in sorted((ledger / "evidence").rglob("*.json")):
    print(f"   {f.relative_to(ledger)}")
PYEOF
echo

"$PY" - "$WORK/dag.json" <<'PYEOF'
import json, sys
dag = json.load(open(sys.argv[1]))
statuses = {e["task_id"]: e["status"] for e in dag}
expected = {"a": "VERIFIED", "b": "VERIFIED", "c": "ROLLED_BACK"}
ok = True
for tid in ("a", "b", "c"):
    got, exp = statuses.get(tid, "?"), expected[tid]
    mark = "OK" if got == exp else "UNEXPECTED"
    ok = ok and got == exp
    print(f"   {tid}: {got}  (expected {exp})  [{mark}]")
print()
if ok:
    print("demo completed: a and b verified through real predicates on real files,")
    print("c failed its predicates at exit 0 and the file_delete rollback restored+confirmed.")
else:
    print("demo UNEXPECTED OUTCOME — statuses above do not match the designed chain.")
sys.exit(0 if ok else 1)
PYEOF
exit $?
