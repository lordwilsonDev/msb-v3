"""Task Contract Executor CLI — run a dag under its permission envelope.

Zero-spend by construction: the StubRunner writes fixture files per the
contract's args and the contract's predicates run for real against what it
wrote. In production the same executor runs behind the workflow endpoint with
the domain-router dispatch as the runner hook.

    execute_task_contract.py --self-test                 # harness self-check
    execute_task_contract.py --execute dag.json --output-root DIR [--ledger-dir DIR]

Exit codes: 0 = pass; 1 = failure/self-test failure; 2 = harness misuse.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from msb_v3.conversation import executor, task_producer  # noqa: E402


def _cmd_self_test() -> int:
    rc1 = executor.run_self_test()
    rc2 = task_producer.run_self_test()
    if rc1 == 0 and rc2 == 0:
        print("self-test: PASS (executor + task producer)")
        return 0
    print(f"self-test: FAIL (executor={rc1} producer={rc2})")
    return 1


def _cmd_execute(args) -> int:
    dag_path = Path(args.execute)
    if not dag_path.exists():
        print(f"FAIL: {args.execute} does not exist")
        return 1
    try:
        dag = json.loads(dag_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"FAIL: cannot parse {args.execute}: {exc}")
        return 1
    if not isinstance(dag, list):
        print("FAIL: dag file must be a JSON list of contract entries")
        return 1

    output_root = Path(args.output_root)
    ledger_dir = Path(args.ledger_dir or task_producer.default_ledger_dir())
    output_root.mkdir(parents=True, exist_ok=True)

    from msb_v3.conversation.task_contract import validate_dag

    errors = validate_dag(dag)
    if errors:
        print("FAIL: dag invalid — " + "; ".join(errors))
        return 1

    updated, result = executor.advance_dag(
        dag, runner=executor.StubRunner(output_root), output_root=output_root,
        ledger_dir=ledger_dir, git_head=task_producer.default_git_head(),
        tenant_id=args.tenant, goal=args.goal, dry_run=args.dry_run,
    )
    if result is None:
        print("nothing READY to execute (preconditions unmet or all advanced)")
        return 0

    print(
        f"executed {result.task_id}: {result.status}"
        + (f" ({result.failure_kind})" if result.failure_kind else "")
    )
    print(f"  claim:    {result.claim_id}")
    print(f"  verdict:  {result.verdict}")
    print(f"  evidence: {result.evidence_ref}")
    if result.event_ref:
        print(f"  event:    TASK_FAILED -> {result.event_ref}")
    print("  reason:   " + result.reason)
    print("  updated dag statuses:")
    for e in updated:
        if isinstance(e, dict) and e.get("task_id"):
            print(f"    {e.get('task_id')}: {e.get('status')}")
    return 0 if result.status in ("VERIFIED", "SUBMITTED") else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Task Contract Executor CLI")
    parser.add_argument("--self-test", action="store_true", help="verify the executor + task producer (no files)")
    parser.add_argument("--execute", metavar="dag.json", help="advance one READY task in this dag file")
    parser.add_argument("--output-root", default=os.getenv("TASK_OUTPUT_ROOT", "."))
    parser.add_argument("--ledger-dir", default=None)
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--goal", default=None)
    parser.add_argument("--dry-run", action="store_true", help="execute but write no evidence")
    args = parser.parse_args(argv)

    if args.self_test:
        return _cmd_self_test()
    if args.execute:
        return _cmd_execute(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
