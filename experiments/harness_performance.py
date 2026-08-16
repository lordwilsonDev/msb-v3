#!/usr/bin/env python3
"""MSB-GOV-EVAL-001 — Performance Experiment (blueprint §11, §12; H5).

Measures governance overhead against the same task under two configurations:

  Config A (baseline): direct-passthrough executor — plain Path.write_bytes,
      no authorization/policy/evidence/budget/approval/audit layers.
      (manifest baseline construction: "governance gates bypassed via a
      direct-passthrough executor"; same filesystem API, same task shape,
      NOT crippled beyond the governance differences under test.)
  Config B (governed): the REAL MSB enforcement path —
      FileWriter.write() (sandbox resolution, size budget, before/after
      evidence hashes, atomic fsync write, postcondition verification)
      + AuditChain.append() (BEGIN IMMEDIATE, prev-hash read, chained hash,
      insert).

Measurement groups (per §11):
  write_baseline : plain passthrough write (no gates)
  write_governed : FileWriter.write (evidence verification included —
                   before-hash read, after-hash, postcondition re-read+hash)
  audit_append   : AuditChain.append in isolation (per-gate absolute cost)
  policy_eval    : authorize_chat in isolation (per-gate absolute cost)
  full_governed  : FileWriter.write + AuditChain.append (complete governed
                   action path — the §12 headline comparison)

Per §12 the headline metric is:
  overhead = (governed_latency - baseline_latency) / baseline_latency
plus absolute ms/action, because a 10% increase on 10ms vs 30s differs.

Reports mean/median/P95/P99/min/max (frozen: perf_statistics_required).

Usage:
    python3 experiments/harness_performance.py [--trials 1000]

Evidence: runs/<date>/raw/performance_<ts>.json + results/performance.csv
"""

from __future__ import annotations

import csv
import json
import os
import resource
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path.home() / ".local/lib/msb-v3"))

from msb_v3.node.filesystem import FileWriter  # noqa: E402
from msb_v3.uac.audit_chain import AuditChain  # noqa: E402
from msb_v3.vesta.models import ABind  # noqa: E402
from msb_v3.vesta.policy import authorize_chat  # noqa: E402

RUN_ROOT = Path(__file__).resolve().parent
DEFAULT_RUN = RUN_ROOT / "runs" / datetime.now(timezone.utc).strftime("%Y-%m-%d")

PAYLOAD = b"benchmark payload " * 8  # ~192 bytes, realistic small artifact


def stats(samples: list) -> dict:
    n = len(samples)
    if n == 0:
        return {}
    s = sorted(samples)
    def q(p: float) -> float:
        # nearest-rank percentile, frozen definition
        idx = max(0, min(n - 1, int(p * n)))
        return s[idx]
    return {
        "n": n,
        "mean_ms": statistics.mean(s) * 1000,
        "median_ms": statistics.median(s) * 1000,
        "p95_ms": q(0.95) * 1000,
        "p99_ms": q(0.99) * 1000,
        "min_ms": s[0] * 1000,
        "max_ms": s[-1] * 1000,
    }


def bench(fn, n: int) -> list:
    samples = []
    for i in range(n):
        t0 = time.perf_counter()
        fn(i)
        samples.append(time.perf_counter() - t0)
    return samples


def main() -> int:
    n = int(sys.argv[sys.argv.index("--trials") + 1]) if "--trials" in sys.argv else 1000
    run_dir = Path(sys.argv[sys.argv.index("--run-dir") + 1]) if "--run-dir" in sys.argv else DEFAULT_RUN
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    (RUN_ROOT / "results").mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    work = Path(tempfile.mkdtemp(prefix="gov-perf-"))
    sandbox = work / "sandbox"
    sandbox.mkdir()
    w = FileWriter(sandbox, max_bytes=1_048_576)
    chain = AuditChain(str(work / "audit.db"))
    bind = ABind.create("perf-session", ["filesystem.write", "chat"], ttl_seconds=3600)

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # ── Config A: baseline direct passthrough ────────────────────────────────
    def baseline_write(i: int) -> None:
        (sandbox / f"b{i}.txt").write_bytes(PAYLOAD)

    # ── Config B gates (isolated absolute costs) ─────────────────────────────
    def governed_write(i: int) -> None:
        w.write(f"g{i}.txt", PAYLOAD)

    def audit_append(i: int) -> None:
        chain.append("perf", "bench", {"i": i})

    def policy_eval(i: int) -> None:
        authorize_chat(bind)

    # ── Config B: full governed action path (write + audit) ──────────────────
    def full_governed(i: int) -> None:
        w.write(f"f{i}.txt", PAYLOAD)
        chain.append("perf", "write", {"i": i, "path": f"f{i}.txt"})

    print(f"trials/group: {n}  (frozen target 1000)\n")
    groups = {
        "write_baseline": bench(lambda i: baseline_write(i), n),
        "write_governed": bench(lambda i: governed_write(i), n),
        "audit_append": bench(lambda i: audit_append(i), n),
        "policy_eval": bench(lambda i: policy_eval(i), n),
        "full_governed": bench(lambda i: full_governed(i), n),
    }
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # ── State verification (blueprint: verify actual state, not just timing) ─
    state_ok = True
    for i in range(min(10, n)):
        p = sandbox / f"f{i}.txt"
        if not p.exists() or p.read_bytes() != PAYLOAD:
            state_ok = False
    chain_valid = chain.verify_chain().get("valid", False)
    n_records = len(chain.get_chain())
    state = {"sample_files_match": state_ok, "audit_chain_valid": chain_valid,
             "audit_records": n_records}

    # ── §12 headline overhead ────────────────────────────────────────────────
    b = statistics.median(groups["write_baseline"])
    g = statistics.median(groups["full_governed"])
    overhead_pct = (g - b) / b * 100 if b else None
    overhead_ms = (g - b) * 1000

    results = {name: stats(s) for name, s in groups.items()}
    results["overhead"] = {
        "definition": "(governed_median - baseline_median) / baseline_median",
        "median_overhead_pct": round(overhead_pct, 2) if overhead_pct is not None else None,
        "absolute_overhead_ms_per_action": round(overhead_ms, 3),
        "per_gate_absolute_ms": {
            "audit_append_median_ms": results["audit_append"]["median_ms"],
            "policy_eval_median_ms": results["policy_eval"]["median_ms"],
            "evidence_in_write_ms": round(
                (results["write_governed"]["median_ms"] - results["write_baseline"]["median_ms"]), 3),
        },
    }

    print("median latency per group (ms/action):")
    for name in groups:
        r = results[name]
        print(f"  {name:16s} mean={r['mean_ms']:8.3f}  p50={r['median_ms']:8.3f}  "
              f"p95={r['p95_ms']:8.3f}  p99={r['p99_ms']:8.3f}  min={r['min_ms']:8.3f}  max={r['max_ms']:8.3f}")
    print(f"\n  §12 overhead (full_governed vs baseline): {overhead_pct:.1f}%  "
          f"= {overhead_ms:.3f} ms/action")
    print(f"  per-gate: audit={results['audit_append']['median_ms']:.3f}ms  "
          f"policy={results['policy_eval']['median_ms']:.3f}ms  "
          f"evidence-in-write={results['overhead']['per_gate_absolute_ms']['evidence_in_write_ms']:.3f}ms")
    print(f"  state: files={state_ok}  audit_chain_valid={chain_valid} records={n_records}")
    print(f"  rss: {rss_before/1024:.0f} -> {rss_after/1024:.0f} KiB ({ (rss_after-rss_before)/1024:.0f} KiB delta)")

    raw = {
        "experiment_id": "MSB-GOV-EVAL-001",
        "subexperiment": "performance",
        "hypothesis": "H5: governance introduces measurable but operationally bounded overhead",
        "git_commit": os.popen("git -C %s rev-parse HEAD" % RUN_ROOT.parent).read().strip(),
        "timestamp": ts,
        "configuration": {
            "A_baseline": "direct-passthrough Path.write_bytes (no governance gates)",
            "B_governed": "FileWriter.write + AuditChain.append (full enforcement path)",
            "fairness": "same filesystem API, same task shape, same environment; baseline not crippled",
        },
        "trials_per_group": n,
        "statistics": results,
        "state_verification": state,
        "resource_usage": {"rss_before_kib": rss_before, "rss_after_kib": rss_after,
                           "rss_delta_kib": rss_after - rss_before},
        "note": "fsync + atomic replace + double-hash are FileWriter integrity gates (governance properties)",
    }
    raw_path = run_dir / "raw" / f"performance_{ts}.json"
    raw_path.write_text(json.dumps(raw, indent=2))

    csv_path = RUN_ROOT / "results" / "performance.csv"
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        cw = csv.writer(f)
        if write_header:
            cw.writerow(["timestamp", "group", "n", "mean_ms", "median_ms", "p95_ms", "p99_ms", "min_ms", "max_ms"])
        for name in groups:
            r = results[name]
            cw.writerow([ts, name, r["n"], r["mean_ms"], r["median_ms"], r["p95_ms"], r["p99_ms"], r["min_ms"], r["max_ms"]])
        cw.writerow([ts, "overhead_pct", "", "", round(overhead_pct, 2) if overhead_pct is not None else "", "", "", "", ""])

    print(f"\n  evidence: {raw_path}")
    return 0 if (state_ok and chain_valid) else 1


if __name__ == "__main__":
    sys.exit(main())
