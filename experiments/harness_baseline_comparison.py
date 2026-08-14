#!/usr/bin/env python3
"""MSB-GOV-EVAL-001 — Baseline Comparison (blueprint §18–§19).

Constructs a deliberately simpler reference system — a direct-passthrough
executor (same filesystem API, same task shape, ZERO governance gates: no
sandbox/policy/evidence/budget/approval/audit) — and runs the IDENTICAL frozen
corpus (gov_corpus.py, seed 20260814, 800 trials) against both the baseline
and governed MSB, on the same hardware/environment, then produces the §19
comparative table.

The strongest result is not "MSB passed more tests" — it is:

    Under identical attack conditions, MSB reduced unauthorized mutations
    from X (baseline) to Y (MSB) while introducing Z ms median governance
    overhead.

Per the frozen manifest, the baseline is NOT crippled beyond the governance
differences under test: `PassthroughExecutor.write` is the same write shape
with the gates removed.

Usage:
    python3 experiments/harness_baseline_comparison.py

Evidence: runs/<date>/raw/baseline_comparison_<ts>.json + results/baseline_comparison.csv
"""

from __future__ import annotations

import csv
import json
import os
import random
import resource
import statistics
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path.home() / ".local/lib/msb-v3"))

from gov_corpus import (  # noqa: E402
    SEED,
    TRIAL_FNS,
    BaselineSurface,
    GovernedSurface,
    run_corpus,
)

from msb_v3.governance.killswitch import KillSwitch  # noqa: E402
from msb_v3.node.filesystem import FileWriter  # noqa: E402
from msb_v3.uac.audit_chain import AuditChain  # noqa: E402
from msb_v3.vesta.approvals import VestaApprovalStore  # noqa: E402
from msb_v3.vesta.evidence import EvidenceStore  # noqa: E402
from msb_v3.vesta.models import VestaFileWriteRequest  # noqa: E402
from msb_v3.vesta.runtime import VestaTaskStore  # noqa: E402
from msb_v3.vesta.write import VestaWriteService  # noqa: E402

RUN_ROOT = Path(__file__).resolve().parent
DEFAULT_RUN = RUN_ROOT / "runs" / datetime.now(timezone.utc).strftime("%Y-%m-%d")


def percentile(samples: list, p: float) -> float:
    s = sorted(samples)
    return s[max(0, min(len(s) - 1, int(p * len(s))))]


def latency_stats(samples: list) -> dict:
    return {
        "median_ms": statistics.median(samples) * 1000,
        "p95_ms": percentile(samples, 0.95) * 1000,
        "p99_ms": percentile(samples, 0.99) * 1000,
    }


def operation_latency(governed: bool, n: int = 1000) -> dict:
    """Direct per-action latency of the canonical write operation with PRE-WARMED
    state (no per-trial tempdir setup confounding): baseline = passthrough write;
    governed = FileWriter.write + AuditChain.append (the full enforcement path)."""
    work = Path(tempfile.mkdtemp(prefix="gov-bc-lat-"))
    root = work / "sandbox"
    root.mkdir()
    samples = []
    if not governed:
        for i in range(n):
            t0 = time.perf_counter()
            (root / f"f{i}.txt").write_bytes(b"benchmark payload " * 8)
            samples.append(time.perf_counter() - t0)
        return latency_stats(samples)
    writer = FileWriter(root, max_bytes=1_048_576)
    chain = AuditChain(str(work / "audit.db"))
    for i in range(n):
        t0 = time.perf_counter()
        writer.write(f"f{i}.txt", b"benchmark payload " * 8)
        chain.append("bench", "write", {"i": i})
        samples.append(time.perf_counter() - t0)
    return latency_stats(samples)


def audit_coverage_probe(governed: bool, n: int = 20) -> dict:
    """AC (manifest formula): governed actions with valid audit records /
    governed actions executed — a rate in [0,1]. Governed = the real service
    path; baseline = passthrough writes with no audit layer."""
    work = Path(tempfile.mkdtemp(prefix="gov-bc-ac-"))
    if not governed:
        root = work / "sandbox"
        root.mkdir()
        for i in range(n):
            (root / f"f{i}.txt").write_bytes(b"x")
        return {"actions_audited": 0, "actions_executed": n, "ac": 0.0}
    audit = AuditChain(str(work / "audit.db"))
    tasks = VestaTaskStore(str(work / "tasks.db"))
    evidence = EvidenceStore(str(work / "evidence"), str(work / "evidence.db"))
    approvals = VestaApprovalStore(str(work / "tasks.db"))
    root = work / "sandbox"
    writer = FileWriter(root, max_bytes=1_048_576)
    kill = KillSwitch(str(work / "kill.db"), audit_chain=audit)
    svc = VestaWriteService(audit, tasks, evidence, approvals, writer, kill)
    audited = 0
    for i in range(n):
        before = len(audit.get_chain())
        pending = svc.submit(VestaFileWriteRequest(session="ac", path=str(root / f"f{i}.txt"), content="x"))
        res = svc.approve_and_execute(pending["approval_id"], "ac-probe")
        assert res["status"] == "completed"
        if len(audit.get_chain()) > before:
            audited += 1
    return {"actions_audited": audited, "actions_executed": n,
            "ac": round(audited / n, 4), "chain_valid": audit.verify_chain().get("valid", False)}


def main() -> int:
    # Self-contained experiment area: every trial mkdtemp (and every escape
    # path that climbs out of a work dir) stays inside a dedicated throwaway
    # root under the system temp dir.
    import tempfile as _tf
    _tf.tempdir = _tf.mkdtemp(prefix="gov-baseline-area-")

    run_dir = Path(sys.argv[sys.argv.index("--run-dir") + 1]) if "--run-dir" in sys.argv else DEFAULT_RUN
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    (RUN_ROOT / "results").mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # ── Identical corpus against both systems ─────────────────────────────────
    # Baseline corpus runs FIRST so its ru_maxrss peak is not inflated by the
    # governed run (ru_maxrss is a process-wide high-water mark).
    print("running corpus against baseline (passthrough)...")
    base_trials, base_m = run_corpus(BaselineSurface())
    base_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print("running corpus against governed MSB...")
    gov_trials, gov_m = run_corpus(GovernedSurface())
    gov_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # ru_maxrss units: bytes on macOS, KiB on Linux — normalize to bytes.
    # Reported as per-system process PEAK after that system's corpus run.
    if sys.platform != "darwin":
        base_rss, gov_rss = base_rss * 1024, gov_rss * 1024

    # ── §19 table ─────────────────────────────────────────────────────────────
    def class_metrics(trials, cls):
        t = [x for x in trials if x["class"] == cls]
        return {
            "attempts": len(t),
            "blocked": sum(1 for x in t if x["blocked"]),
            "mutated": sum(1 for x in t if x.get("mutated")),
        }

    gov_lat = operation_latency(governed=True)
    base_lat = operation_latency(governed=False)
    gov_ac = audit_coverage_probe(governed=True)
    base_ac = audit_coverage_probe(governed=False)

    # recovery failures: violations that mutated with no quarantine/recovery record
    def recovery_failures(trials):
        return sum(1 for x in trials if x["expected_block"] and x.get("mutated"))

    # V2 trials check policy without performing a concrete mutation, so the
    # frozen taxonomy classifies baseline V2 as INDETERMINATE; the honest
    # table count for "policy violations allowed" is attempts - blocked.
    def allowed_through(trials, cls):
        t = [x for x in trials if x["class"] == cls]
        return len(t) - sum(1 for x in t if x["blocked"])

    table = {
        "unauthorized_actions": {"baseline": class_metrics(base_trials, "V1")["mutated"],
                                 "msb": class_metrics(gov_trials, "V1")["mutated"]},
        "policy_violations": {"baseline": allowed_through(base_trials, "V2"),
                              "msb": allowed_through(gov_trials, "V2")},
        "false_allows": {"baseline": base_m["n_false_allows"], "msb": gov_m["n_false_allows"]},
        "false_denies": {"baseline": base_m["n_false_denies"], "msb": gov_m["n_false_denies"]},
        "audit_coverage": {"baseline": base_ac["ac"], "msb": gov_ac["ac"]},
        "evidence_failures_detected": {
            "baseline": class_metrics(base_trials, "V4")["blocked"],
            "msb": class_metrics(gov_trials, "V4")["blocked"]},
        "recovery_failures": {"baseline": recovery_failures(base_trials),
                              "msb": recovery_failures(gov_trials)},
        "median_latency_ms": {"baseline": base_lat["median_ms"], "msb": gov_lat["median_ms"]},
        "p95_latency_ms": {"baseline": base_lat["p95_ms"], "msb": gov_lat["p95_ms"]},
        "p99_latency_ms": {"baseline": base_lat["p99_ms"], "msb": gov_lat["p99_ms"]},
        "resource_delta_bytes": {"baseline": base_rss, "msb": gov_rss},
    }

    x = base_m["n_false_allows"]
    y = gov_m["n_false_allows"]
    z = gov_lat["median_ms"] - base_lat["median_ms"]
    headline = {
        "statement": (f"Under identical attack conditions, MSB reduced unauthorized mutations "
                      f"from {x} (baseline) to {y} (MSB) while introducing "
                      f"{z:.3f} ms median governance overhead per action."),
        "unauthorized_mutations_baseline": x,
        "unauthorized_mutations_msb": y,
        "median_overhead_ms": round(z, 4),
    }

    print("\n§19 comparative table:")
    print(f"  {'metric':26s} {'baseline':>10s} {'MSB':>10s}")
    for k, v in table.items():
        if isinstance(v, dict):
            print(f"  {k:26s} {v['baseline']!s:>10s} {v['msb']!s:>10s}")
        else:
            print(f"  {k:26s} {v!s:>10s}")
    # auditable per-class baseline outcomes (V2/V7 classify INDETERMINATE by the
    # frozen taxonomy — policy/tamper checks without a concrete mutation)
    base_by_class = {c: dict(Counter(t["outcome"] for t in base_trials if t["class"] == c)) for c in TRIAL_FNS}
    gov_by_class = {c: dict(Counter(t["outcome"] for t in gov_trials if t["class"] == c)) for c in TRIAL_FNS}
    print("  baseline per-class outcomes:", json.dumps(base_by_class))
    print("  governed per-class outcomes:", json.dumps(gov_by_class))
    print(f"\n  {headline['statement']}")

    raw = {
        "experiment_id": "MSB-GOV-EVAL-001",
        "subexperiment": "baseline_comparison",
        "hypothesis": "§18-19: governed MSB materially outperforms the governance-bypassed baseline "
                      "under identical attack conditions",
        "git_commit": os.popen("git -C %s rev-parse HEAD" % RUN_ROOT.parent).read().strip(),
        "timestamp": ts,
        "corpus": {"seed": SEED, "trials_per_system": 800, "shared": "gov_corpus.py — identical inputs"},
        "baseline_construction": "direct-passthrough executor: same filesystem API, same task shape, "
                                 "zero governance gates (no sandbox/policy/evidence/budget/approval/audit); "
                                 "not crippled beyond the governance differences under test",
        "governed_metrics": gov_m,
        "baseline_metrics": base_m,
        "table": table,
        "headline": headline,
        "latency": {"baseline": base_lat, "msb": gov_lat},
        "audit_coverage_detail": {"baseline": base_ac, "msb": gov_ac},
        "governed_trials": gov_trials,
        "baseline_trials": base_trials,
    }
    raw_path = run_dir / "raw" / f"baseline_comparison_{ts}.json"
    raw_path.write_text(json.dumps(raw, indent=2))

    csv_path = RUN_ROOT / "results" / "baseline_comparison.csv"
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        cw = csv.writer(f)
        if write_header:
            cw.writerow(["timestamp", "metric", "baseline", "msb"])
        for k, v in table.items():
            cw.writerow([ts, k, v["baseline"] if isinstance(v, dict) else v,
                         v["msb"] if isinstance(v, dict) else ""])
        cw.writerow([ts, "headline", headline["statement"], ""])

    print(f"\n  evidence: {raw_path}")
    return 0 if gov_m["n_false_allows"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
