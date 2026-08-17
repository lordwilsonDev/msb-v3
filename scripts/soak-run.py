#!/usr/bin/env python3
"""M5 soak run — realistic workload sample with measured scoreboard metrics.

Closes the M5 "soak run is completed" exit criterion: a repeatable run report
covers a meaningful workload sample (default 60 seeded runs through the real
executor + ActionGate + audit chain), asserting the scoreboard targets:

    completion_rate       >= 0.90 on supported cases
    unsafe_escape_rate    == 0 (no BLOCK/REVIEW action executed its tool)
    evidence_completeness >= 0.98 (every gate refusal produced an audit record)
    recovery_rate         >= 0.80 (retried-then-succeeded / all retried)

Hermetic: fake tool providers, no model, no network — deterministic per seed,
so the report is reproducible and safe to run in CI.

Usage:
    python3 scripts/soak-run.py [--runs N] [--seed N] [--out FILE]

Exit code: 0 when every target is met, 1 otherwise (a soak that misses a
target is a gate failure, not a report).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from msb_v3.observability.soak import run_soak  # noqa: E402


def _targets_met(report: dict) -> bool:
    metrics = report["metrics"]
    targets = report["targets"]
    return (
        metrics["completion_rate"] >= targets["completion_rate_min"]
        and metrics["unsafe_escape_rate"] <= targets["unsafe_escape_rate_max"]
        and metrics["evidence_completeness"] >= targets["evidence_completeness_min"]
        and metrics["recovery_rate"] >= targets["recovery_rate_min"]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="M5 soak run (see module docstring)")
    parser.add_argument("--runs", type=int, default=60, help="number of seeded runs (default 60)")
    parser.add_argument("--seed", type=int, default=7, help="deterministic seed (default 7)")
    parser.add_argument("--out", metavar="FILE", default="",
                        help="output JSON path (default artifacts/soak-report-<ts>.json)")
    args = parser.parse_args()

    report = asyncio.run(run_soak(n_runs=args.runs, seed=args.seed))
    data = report.to_dict()
    data["seed"] = args.seed
    data["run_at"] = datetime.now(timezone.utc).isoformat()

    out = Path(args.out) if args.out else ROOT / "artifacts" / (
        f"soak-report-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2))

    ok = _targets_met(data)
    metrics = data["metrics"]
    print(json.dumps(data, indent=2))
    print(f"\n[soak] report -> {out}")
    print(f"[soak] completion={metrics['completion_rate']} "
          f"unsafe_escape={metrics['unsafe_escape_rate']} "
          f"evidence={metrics['evidence_completeness']} "
          f"recovery={metrics['recovery_rate']} "
          f"escalation={metrics['escalation_rate']} "
          f"retries={metrics['total_retries']} "
          f"p50={metrics['p50_latency_s']}s p95={metrics['p95_latency_s']}s")
    print(f"[soak] VERDICT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
