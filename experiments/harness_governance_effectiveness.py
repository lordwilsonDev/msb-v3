#!/usr/bin/env python3
"""MSB-GOV-EVAL-001 — Governance Effectiveness (blueprint §6, §7).

Generates the frozen adversarial corpus (100 trials per violation class,
8 classes = 800 trials, seed `20260814`) and runs each against the REAL MSB
governance surface (FileWriter sandbox + Vesta policy + AuditChain). Every
trial receives exactly one primary outcome from the frozen taxonomy:

    BLOCKED_CORRECTLY | ALLOWED_CORRECTLY | FALSE_ALLOW | FALSE_DENY
    | SYSTEM_ERROR | INDETERMINATE

The corpus + classification live in `gov_corpus.py` so the IDENTICAL inputs
also run against the governance-bypassed baseline in
`harness_baseline_comparison.py` (§18–19).

Metrics (frozen formulas): APR, FAR, FDR, GC, AC, EIR.

Usage:
    python3 experiments/harness_governance_effectiveness.py [--trials 800]

Evidence: runs/<date>/raw/governance_<ts>.json + results/governance.csv
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path.home() / ".local/lib/msb-v3"))

from gov_corpus import (  # noqa: E402
    SEED,
    TRIAL_FNS,
    GovernedSurface,
    classify,
    run_corpus,
)

RUN_ROOT = Path(__file__).resolve().parent
DEFAULT_RUN = RUN_ROOT / "runs" / datetime.now(timezone.utc).strftime("%Y-%m-%d")


def main() -> int:
    n = int(sys.argv[sys.argv.index("--trials") + 1]) if "--trials" in sys.argv else 800
    run_dir = Path(sys.argv[sys.argv.index("--run-dir") + 1]) if "--run-dir" in sys.argv else DEFAULT_RUN
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    (RUN_ROOT / "results").mkdir(parents=True, exist_ok=True)

    trials, metrics = run_corpus(GovernedSurface(), n)
    outcomes = Counter(t["outcome"] for t in trials)
    by_class = {c: dict(Counter(t["outcome"] for t in trials if t["class"] == c)) for c in TRIAL_FNS}

    print(f"corpus: {len(trials)} trials (seed {SEED}), {max(1, n // 8)}/class")
    for cls in TRIAL_FNS:
        print(f"  {cls}: {by_class[cls]}")
    print(f"\n  APR = {metrics['APR']:.2%}   FAR = {metrics['FAR']:.2%}   FDR = {metrics['FDR']:.2%}")
    if metrics["n_indeterminate"]:
        print(f"  ⚠ {metrics['n_indeterminate']} INDETERMINATE trials — each must be investigated per frozen policy")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw = {
        "experiment_id": "MSB-GOV-EVAL-001",
        "subexperiment": "governance_effectiveness",
        "git_commit": os.popen("git -C %s rev-parse HEAD" % RUN_ROOT.parent).read().strip(),
        "timestamp": ts, "random_seed": SEED, "input_corpus_version": "gov-corpus-v1",
        "metrics": metrics, "outcomes": dict(outcomes), "by_class": by_class,
        "trials": trials,
    }
    raw_path = run_dir / "raw" / f"governance_{ts}.json"
    raw_path.write_text(json.dumps(raw, indent=2))

    csv_path = RUN_ROOT / "results" / "governance.csv"
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["timestamp", "trial_class", "expected_block", "blocked", "mutated", "outcome"])
        for t in trials:
            w.writerow([ts, t["class"], t["expected_block"], t["blocked"], t.get("mutated"), t["outcome"]])

    print(f"  evidence: {raw_path}")
    return 0 if metrics["n_false_allows"] == 0 and metrics["n_indeterminate"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
