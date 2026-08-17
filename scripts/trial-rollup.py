#!/usr/bin/env python3
"""M6 trial weekly rollup — turn the operating ledger into review metrics.

Parses `docs/blueprints/convergence-to-12/operating-ledger-entries.md` and
emits the Friday-review numbers in one command (m6-trial.md cadence):

    * task count + completion rate (PASS/Completed vs FAIL/denied/blocked)
    * interventions by class (approve / fix / retry / bypass)
    * median MSB time vs median baseline estimate
    * evidence-record-useful count (High vs not)

Entry format is the one `scripts/trial-log.sh` writes plus the manual
template in operating-ledger.md — fields are prose lines, so parsing is
regex-over-headers, tolerant of both shapes:

    **MSB result:** **PASS.** run `...`, deterministic hash `...`, ~61s ...
    **Baseline:** 12 min        |   ~15 min (grep vault, ...)   |  n/a

Usage:
    python3 scripts/trial-rollup.py                 # summary to stdout
    python3 scripts/trial-rollup.py --json          # machine-readable
    python3 scripts/trial-rollup.py --entry 013     # one entry's numbers
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

LEDGER = Path(__file__).resolve().parent.parent / "docs/blueprints/convergence-to-12/operating-ledger-entries.md"

# Outcome classification: the result line decides complete/failed.
_COMPLETE_MARKERS = ("**PASS.**", "**Completed.**", "**Correctly denied", "**Correctly blocked", "**Pipeline ran")
_FAIL_MARKERS = ("**Failed", "**Correctly denied", "**Correctly blocked")
# ^ denied/blocked are intentional fail-closed outcomes — counted separately.

_INTERVENTION_CLASSES = ("approve", "fix", "retry", "bypass")


def _parse_entry(block: str) -> dict:
    """Extract the review-relevant fields from one ledger entry block."""
    e: dict = {"interventions": [], "evidence": "?", "baseline_min": None, "msb_sec": None}

    m = re.search(r"^## Entry (\d+)", block, re.M)
    e["num"] = int(m.group(1)) if m else None

    m = re.search(r"\*\*Task:\*\*\s*(.+)", block)
    e["task"] = (m.group(1).strip() if m else "")

    # MSB result may span continuation lines (trial-log entries wrap the
    # hash + duration onto line 2). Capture until the next field header —
    # headers here are **Field:** i.e. bold INCLUDING the colon.
    m = re.search(r"\*\*MSB result:\*\*\s*((?:[^\n]|\n(?!\*\*[A-Za-z ]+:\*\*))*)", block)
    e["result"] = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

    # Outcome: PASS/Completed = success; explicit FAIL = failure; the
    # denied/blocked safety cases are intentional (success of the guard).
    # "ran, failed closed" (entry 005) did not reach its intended end state
    # (no merge) — counted as failed, honestly.
    outcome = "?"
    if re.search(
        r"\*\*PASS\.\*\*|\*\*Completed\.\*\*|\*\*Correctly (denied|blocked)\.\*\*|\*\*Fixed\.\*\*|Root cause found",
        e["result"],
    ):
        outcome = "complete"
    elif re.search(r"\*\*Failed|failed closed", e["result"]):
        outcome = "failed"
    e["outcome"] = outcome

    # Intervention: the line may be "None", "None — ...", "Yes — ...", or
    # "The plumbing fixes + ..." (entry 007). Classify by keyword.
    m = re.search(r"\*\*Intervention:\*\*\s*(.+)", block)
    inter = m.group(1).strip() if m else ""
    low = inter.lower()
    if low.startswith("none") or inter in ("—", ""):
        e["interventions"] = []
    else:
        found = [c for c in _INTERVENTION_CLASSES if c in low]
        e["interventions"] = found or ["fix"]  # described intervention = fix/repair work

    # Evidence may be bold-wrapped (**High**) or plain (High).
    m = re.search(r"\*\*Evidence quality:\*\*\s*\*{0,2}(\w+)", block)
    e["evidence"] = m.group(1) if m else "?"

    # Baseline time: "12 min", "~15 min (grep ...)", "30m", "n/a".
    m = re.search(r"\*\*Baseline:\*\*\s*([^\n]+)", block)
    base = m.group(1) if m else ""
    bm = re.search(r"(\d+)\s*(?:min|m)\b", base)
    if bm:
        e["baseline_min"] = float(bm.group(1))

    # MSB wall time: "~61s on qwen3:8b", "~24s", "~52s".
    sm = re.search(r"~(\d+)s\b", e["result"])
    if sm:
        e["msb_sec"] = float(sm.group(1))

    return e


def rollup(entries: list[dict]) -> dict:
    n = len(entries)
    complete = [e for e in entries if e["outcome"] == "complete"]
    failed = [e for e in entries if e["outcome"] == "failed"]
    intervened = [e for e in entries if e["interventions"]]
    evidence_high = [e for e in entries if e["evidence"].lower().startswith("high")]
    baseline = [e["baseline_min"] for e in entries if e["baseline_min"] is not None]
    msb = [e["msb_sec"] for e in entries if e["msb_sec"] is not None]

    classes: dict[str, int] = {}
    for e in entries:
        for c in e["interventions"]:
            classes[c] = classes.get(c, 0) + 1

    return {
        "entries": n,
        "complete": len(complete),
        "failed": len(failed),
        "completion_rate": round(len(complete) / n, 3) if n else None,
        "intervened": len(intervened),
        "intervention_classes": classes,
        "median_baseline_min": round(statistics.median(baseline), 1) if baseline else None,
        "median_msb_sec": round(statistics.median(msb), 1) if msb else None,
        "evidence_high": len(evidence_high),
        "evidence_rate": round(len(evidence_high) / n, 3) if n else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--entry", type=int, help="show one entry's parsed numbers")
    args = ap.parse_args()

    text = LEDGER.read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^## Entry ", text)[1:]
    entries = [_parse_entry("## Entry " + b) for b in blocks]

    if args.entry:
        e = next((x for x in entries if x["num"] == args.entry), None)
        if not e:
            print(f"no entry {args.entry}", file=sys.stderr)
            return 1
        print(json.dumps(e, indent=2))
        return 0

    r = rollup(entries)
    if args.json:
        print(json.dumps(r, indent=2))
        return 0

    print(f"=== M6 weekly rollup — {LEDGER.name} ({len(entries)} entries) ===")
    print(f"tasks logged:        {r['entries']}")
    print(f"completed:           {r['complete']}  (rate {r['completion_rate']})")
    print(f"failed:              {r['failed']}")
    print(f"human intervention:  {r['intervened']}  by class {r['intervention_classes'] or '{}'}")
    print(f"median baseline:     {r['median_baseline_min']} min  |  median MSB: {r['median_msb_sec']} s")
    if r["median_baseline_min"] and r["median_msb_sec"]:
        saved = r["median_baseline_min"] * 60 - r["median_msb_sec"]
        print(f"median time saved:   ~{saved:.0f} s per task (baseline − MSB)")
    print(f"evidence high:       {r['evidence_high']}  (rate {r['evidence_rate']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
