"""Dream Big Blue — live end-to-end demo of handle_this() against the real stack.

Completes the Phase 0B/Phase 1 parked follow-up: "live end-to-end demo run
against a real server". Runs the real loop (interpret -> plan -> safe-execute ->
grounded-verify -> evidence) against live Ollama qwen3 + the wilson-vault
Qdrant tenant, writes the client brief to a timestamped demo dir, and prints a
human-readable summary of the trace (verdict, tasks, grounded receipts, cost,
replay hash).

Usage:
    python scripts/demo_live_run.py "Research the vault and write a client brief about the sovereign agentic runtime."
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from msb_v3.agent.handle import handle  # noqa: E402
from msb_v3.agent.trace import compute_deterministic_hash  # noqa: E402

DEFAULT_REQUEST = "Research the vault and write a client brief about the sovereign agentic runtime."


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _task_summary(trace: dict) -> None:
    print("\n=== EXECUTION (task DAG, in order) ===")
    for i, execution in enumerate(trace.get("execution", []), 1):
        task_id = execution.get("task_id")
        verification = execution.get("verification") or {}
        receipt = "—"
        if verification:
            receipt = (
                f"kind={verification.get('kind')} trust={verification.get('trust')} "
                f"verdict={verification.get('verdict')} conf={verification.get('confidence')}"
            )
        # Trace execution entries carry ok/error, not a status field.
        status_str = "done" if execution.get("ok") else f"FAIL{(execution.get('error') or '')[:40]}"
        task_id_str = task_id or "?"
        print(f"  {i}. [{status_str:>9}] {task_id_str:28} verify: {receipt}")


async def main() -> int:
    request = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REQUEST

    stamp = _stamp()
    out_dir = Path.home() / "Desktop" / "out" / f"dbb-demo-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Request : {request}")
    print(f"Output  : {out_dir}")

    result = await handle(
        request,
        tenant="wilson-vault",
        approve=True,
        output_dir=str(out_dir),
        session="demo-run",
    )

    # persist whatever evidence exists before reporting, even on failure
    evidence = out_dir / "trace.json"
    if result.trace:
        evidence.write_text(json.dumps(result.trace, indent=2, default=str))

    if not result.ok:
        print(f"\n❌ RUN FAILED — verdict={result.verdict} error={result.error}")
        return 1

    trace = result.trace
    outcome = trace.get("outcome", {})

    print("\n✅ LOOP CLOSED — Handle this. end-to-end")
    print(f"  run_id            : {result.run_id}")
    print(f"  verdict           : {result.verdict}")
    print(f"  deterministic_hash: {result.deterministic_hash}")
    print(f"  replay check      : {'MATCH (content-addressed)' if compute_deterministic_hash(trace) == result.deterministic_hash else 'MISMATCH!'}")
    print(f"  completion_tokens : {outcome.get('completion_tokens')}")
    print(f"  estimated_cost_usd: {outcome.get('estimated_cost_usd')}")

    _task_summary(trace)

    briefs = sorted(out_dir.glob("*.md"))
    if briefs:
        print("\n=== WRITTEN BRIEF ===")
        print(f"  {briefs[0].name} ({briefs[0].stat().st_size} bytes)")
        print("-" * 72)
        print(briefs[0].read_text(errors="replace")[:3000])
        print("-" * 72)
    else:
        print("\n(no .md artifact found in the demo output dir)")

    print(f"\nEvidence trace written to: {evidence}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
