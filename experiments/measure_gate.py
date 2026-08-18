#!/usr/bin/env python3
"""MSB-GATE-EVAL-001 — measure the MoIE quick-reject gate's precision and recall.

Runs the frozen corpus (``tests/contracts/gate_corpus.py``) through the exact
decision the gate makes — ``MoIEController().analyze(claim)`` with NO
high-impact escalation, BLOCK = deny — and reports:

- precision / recall / F1 of the gate alone (is it useful, or merely aggressive?)
- the confusion matrix
- every miss (dangerous request the gate let through) and every false positive
  (benign request the gate denied), by category

The gate is a PRE-FILTER, not the security boundary. A miss here is only a
safety failure if the rest of the stack (plan-parser capability whitelist +
ActionGate tier table + A8 taint) also lets the action through — that layered
proof lives in ``tests/contracts/test_layered_boundary.py``. This script
measures the gate's *own* numbers, honestly.

Usage:  PYTHONPATH=src python experiments/measure_gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "tests" / "contracts"))

from gate_corpus import CORPUS  # noqa: E402

from msb_v3.moie import MoIEController  # noqa: E402


def gate_verdict(claim: str) -> str:
    """The exact decision the quick-reject gate makes: MoIE analyze with no
    high-impact escalation. Returns BLOCK (deny) or the other verdicts."""
    return MoIEController().analyze(claim).verdict


def measure() -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    """Run the corpus through the gate. Returns (per-item rows, metrics)."""
    rows: List[Dict[str, object]] = []
    tp = fp = tn = fn = 0
    for entry in CORPUS:
        verdict = gate_verdict(str(entry["claim"]))
        blocked = verdict == "BLOCK"
        dangerous = bool(entry["dangerous"])
        if dangerous and blocked:
            tp += 1
            outcome = "TP"
        elif dangerous and not blocked:
            fn += 1
            outcome = "FN"
        elif not dangerous and blocked:
            fp += 1
            outcome = "FP"
        else:
            tn += 1
            outcome = "TN"
        rows.append({**entry, "verdict": verdict, "blocked": blocked, "outcome": outcome})

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)) if (precision and recall) else None
    metrics: Dict[str, object] = {
        "corpus_version": "MSB-GATE-CORPUS-001",
        "n": len(rows),
        "n_dangerous": tp + fn,
        "n_benign": tn + fp,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "gate_only": True,  # downstream layers measured separately
    }
    return rows, metrics


def _report(rows: List[Dict[str, object]], metrics: Dict[str, object]) -> str:
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("MSB-GATE-EVAL-001 — quick-reject gate precision/recall")
    lines.append("=" * 72)
    lines.append(
        f"n={metrics['n']}  dangerous={metrics['n_dangerous']}  benign={metrics['n_benign']}"
    )
    lines.append(
        f"precision={metrics['precision']}  recall={metrics['recall']}  f1={metrics['f1']}"
    )
    lines.append(
        f"confusion: TP={metrics['tp']}  FP={metrics['fp']}  TN={metrics['tn']}  FN={metrics['fn']}"
    )
    lines.append("")
    lines.append("MISSES (dangerous, gate proceeded) — layered defense must catch these:")
    for r in [r for r in rows if r["outcome"] == "FN"]:
        lines.append(f"  [{r['id']}/{r['category']}] verdict={r['verdict']}  {r['claim']!r}")
    lines.append("")
    lines.append("FALSE POSITIVES (benign, gate denied):")
    for r in [r for r in rows if r["outcome"] == "FP"]:
        lines.append(f"  [{r['id']}/{r['category']}] {r['claim']!r}")
    lines.append("")
    lines.append("Per-category:")
    from collections import defaultdict

    by_cat: Dict[str, Dict[str, int]] = defaultdict(lambda: {"n": 0, "blocked": 0})
    for r in rows:
        by_cat[r["category"]]["n"] += 1
        if r["blocked"]:
            by_cat[r["category"]]["blocked"] += 1
    for cat, counts in by_cat.items():
        lines.append(
            f"  {cat:22s} n={counts['n']:2d}  blocked={counts['blocked']:2d}"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    rows, metrics = measure()
    print(_report(rows, metrics))


if __name__ == "__main__":
    main()
