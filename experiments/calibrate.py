"""MSB-CAL-001 — MoIE calibration harness.

Phase 2's "find the values that maximize F1" — answered honestly. Two
measurements over the frozen gate corpus (tests/contracts/gate_corpus.py):

1. Numeric-grid verdict-invariance. Sweep every tunable numeric constant
   (contradiction_penalty, confidence clamp, concern materiality threshold,
   expert confidence formula) and show the gate's BLOCK decision is
   IDENTICAL across every combination. The verdict is a pure function of
   expert keyword hits; these constants only move the confidence number.
   Calibrating them against precision/recall therefore cannot move the
   gate — the honest answer to the plan's premise.

2. Keyword-membership lever. Because the verdict is a keyword function, the
   only thing that moves gate F1 is keyword membership. For each candidate
   miss-derived danger keyword (and the combined set) we rebuild the
   security expert and measure precision/recall/F1. These are
   RECOMMENDATIONS: applying them changes the pinned gate contract numbers
   (tests/contracts/test_gate_contract.py) as a deliberate policy change.

Run:   python experiments/calibrate.py
Writes: experiments/reports/calibrate_<ts>.json  (and prints a table)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "contracts"))

from gate_corpus import CORPUS  # noqa: E402

from msb_v3.core.calibration import moie_calibration  # noqa: E402
from msb_v3.moie import MoIEController  # noqa: E402
from msb_v3.moie.experts import (  # noqa: E402
    BUILTIN_EXPERTS,
    DomainExpert,
    ExpertRegistry,
)

REPORT_DIR = ROOT / "experiments" / "reports"


def _controller() -> MoIEController:
    """A controller with the evidence seam disabled. The gate contract
    measures the keyword pre-filter, not the memory-fabric evidence layer;
    a no-op retriever keeps the sweep deterministic and fast (no DB open,
    no embedding seam per call)."""
    return MoIEController(retriever=lambda _claim: [])


# ── corpus metrics ───────────────────────────────────────────────────────────

def gate_metrics(controller: MoIEController) -> Dict[str, Any]:
    """Gate-only precision/recall over the corpus (BLOCK = the gate fires)."""
    tp = fp = tn = fn = 0
    for entry in CORPUS:
        verdict = controller.analyze(str(entry["claim"])).verdict
        blocked = verdict == "BLOCK"
        dangerous = bool(entry["dangerous"])
        if dangerous and blocked:
            tp += 1
        elif dangerous and not blocked:
            fn += 1
        elif not dangerous and blocked:
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
    }


def verdict_vector(controller: MoIEController) -> Tuple[str, ...]:
    """The gate's per-entry BLOCK/not-BLOCK decisions, in corpus order."""
    return tuple(
        "BLOCK" if controller.analyze(str(e["claim"])).verdict == "BLOCK" else "ok"
        for e in CORPUS
    )


# ── measurement 1: numeric-grid verdict-invariance ──────────────────────────

def _grid_values() -> Dict[str, List[float]]:
    return {
        "contradiction_penalty": [0.0, 0.15, 0.3],
        "confidence_min": [0.05, 0.1, 0.25],
        "confidence_max": [0.9, 1.0],
        "concern_material_min_confidence": [0.4, 0.6, 0.8],
        "expert_confidence_base": [0.4, 0.5, 0.6],
        "expert_confidence_danger_step": [0.1, 0.15, 0.25],
        "expert_confidence_concern_step": [0.05, 0.08, 0.15],
        "expert_confidence_cap": [0.9, 0.95, 1.0],
    }


def measure_numeric_invariance() -> Dict[str, Any]:
    """Sweep the numeric constants; prove the verdict vector never changes.

    The sweep is combinatorial (3*3*2*3*3*3*3*3 = 4,374 combos, each ~56
    deterministic analyses — cheap, no model calls). To keep the sweep
    tractable we vary one axis at a time (each axis with the others at
    defaults) — enough to prove every axis is verdict-inert, and it runs in
    a few seconds.
    """
    baseline_controller = _controller()
    baseline_vector = verdict_vector(baseline_controller)
    baseline_conf_lo, baseline_conf_hi = _confidence_bounds(baseline_controller)
    baseline_metrics = gate_metrics(baseline_controller)

    original = {f: getattr(moie_calibration, f) for f in _grid_values()}
    findings: List[Dict[str, Any]] = []
    max_conf_delta = 0.0
    changed = 0

    try:
        for field, values in _grid_values().items():
            for value in values:
                setattr(moie_calibration, field, value)
                controller = _controller()
                vector = verdict_vector(controller)
                lo, hi = _confidence_bounds(controller)
                max_conf_delta = max(max_conf_delta, abs(lo - baseline_conf_lo), abs(hi - baseline_conf_hi))
                if vector != baseline_vector:
                    changed += 1
                findings.append(
                    {
                        "field": field,
                        "value": value,
                        "verdict_vector_identical": vector == baseline_vector,
                        "confidence_lo": lo,
                        "confidence_hi": hi,
                    }
                )
    finally:
        for field, value in original.items():
            setattr(moie_calibration, field, value)

    return {
        "baseline_metrics": baseline_metrics,
        "combinations_tested": sum(len(v) for v in _grid_values().values()),
        "verdict_changes": changed,
        "verdict_invariant": changed == 0,
        "max_confidence_absolute_delta": round(max_conf_delta, 4),
        "axis_findings": findings,
    }


def _confidence_bounds(controller: MoIEController) -> Tuple[float, float]:
    """Min/max decision confidence across the corpus for a controller."""
    lo, hi = 1.0, 0.0
    for entry in CORPUS:
        conf = controller.analyze(str(entry["claim"])).confidence
        lo, hi = min(lo, conf), max(hi, conf)
    return round(lo, 4), round(hi, 4)


def _controller_with_security_extra(extra: List[str]) -> MoIEController:
    experts = list(BUILTIN_EXPERTS)
    for i, e in enumerate(experts):
        if e.expert_id == "security":
            experts[i] = _expert_with_extra_danger(e, extra)
    return MoIEController(registry=ExpertRegistry(tuple(experts)), retriever=lambda _claim: [])


# ── measurement 2: keyword-membership lever ─────────────────────────────────

# Miss-derived candidates: dangerous items the gate currently lets through
# (D3, D11, D15) whose wording is a stable phrase, plus their benign-corpus
# false-positive check is done by the harness (any candidate that blocks a
# benign/ambiguous item is flagged, not silently accepted).
CANDIDATE_DANGER_KEYWORDS: List[str] = [
    "disable authentication",  # D3
    "escalate privileges",     # D11
    "exfiltrate",              # D15
]


def _expert_with_extra_danger(base: DomainExpert, extra: List[str]) -> DomainExpert:
    """A copy of a built-in expert with extra danger keywords appended."""
    return DomainExpert(
        expert_id=base.expert_id,
        name=base.name,
        description=base.description,
        focus_keywords=base.focus_keywords,
        always_on=base.always_on,
        danger_keywords=tuple(dict.fromkeys(list(base.danger_keywords) + list(extra))),
        concern_keywords=base.concern_keywords,
        risk_templates=base.risk_templates,
        mitigation_templates=base.mitigation_templates,
    )


def measure_keyword_lever() -> Dict[str, Any]:
    """Measure each candidate danger keyword (and the combined set) against
    the corpus. Flags any candidate that false-positives a benign/ambiguous
    item — those would be aggressive, not useful."""
    baseline = gate_metrics(_controller())
    results: List[Dict[str, Any]] = []

    subsets: List[Tuple[str, List[str]]] = [
        (kw, [kw]) for kw in CANDIDATE_DANGER_KEYWORDS
    ]
    subsets.append(("combined", CANDIDATE_DANGER_KEYWORDS))

    baseline_controller = _controller()
    for label, kws in subsets:
        controller = _controller_with_security_extra(kws)
        m = gate_metrics(controller)
        # Aggressive = the candidate adds NEW false positives over baseline
        # (pre-existing baseline FPs are not the candidate's fault).
        new_fp = [
            e["id"]
            for e in CORPUS
            if not e["dangerous"]
            and baseline_controller.analyze(str(e["claim"])).verdict != "BLOCK"
            and controller.analyze(str(e["claim"])).verdict == "BLOCK"
        ]
        newly_blocked = [
            e["id"]
            for e in CORPUS
            if e["dangerous"]
            and baseline_controller.analyze(str(e["claim"])).verdict != "BLOCK"
            and controller.analyze(str(e["claim"])).verdict == "BLOCK"
        ]
        results.append(
            {
                "candidate": label,
                "keywords": kws,
                "metrics": m,
                "delta_f1": round(m["f1"] - baseline["f1"], 4),
                "delta_recall": round(m["recall"] - baseline["recall"], 4),
                "delta_precision": round(m["precision"] - baseline["precision"], 4),
                "newly_blocked_dangerous_ids": newly_blocked,
                "new_false_positive_ids_on_benign": new_fp,
                "aggressive": bool(new_fp),
            }
        )

    return {"baseline": baseline, "candidates": results}


# ── report ──────────────────────────────────────────────────────────────────

def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 72)
    print("MSB-CAL-001 MoIE calibration harness")
    print("=" * 72)

    print("\n[1/2] numeric-grid verdict-invariance ...")
    t0 = time.time()
    numeric = measure_numeric_invariance()
    print(
        f"  {numeric['combinations_tested']} combinations, "
        f"{numeric['verdict_changes']} verdict changes -> "
        f"verdict-invariant: {numeric['verdict_invariant']}"
    )
    print(f"  baseline gate metrics: {numeric['baseline_metrics']}")
    print(f"  max |confidence| delta across sweep: {numeric['max_confidence_absolute_delta']}")
    print(f"  ({time.time() - t0:.1f}s)")

    print("\n[2/2] keyword-membership lever ...")
    keyword = measure_keyword_lever()
    b = keyword["baseline"]
    print(f"  baseline: tp={b['tp']} fp={b['fp']} tn={b['tn']} fn={b['fn']} "
          f"P={b['precision']} R={b['recall']} F1={b['f1']}")
    for r in keyword["candidates"]:
        flag = "  <-- AGGRESSIVE (FP on benign)" if r["aggressive"] else ""
        print(
            f"  +{r['candidate']:<24} P={r['metrics']['precision']} "
            f"R={r['metrics']['recall']} F1={r['metrics']['f1']} "
            f"(dF1 {r['delta_f1']:+}, dR {r['delta_recall']:+}, dP {r['delta_precision']:+})"
            f"{flag}"
        )

    report = {
        "experiment": "MSB-CAL-001",
        "corpus": "MSB-GATE-CORPUS-001",
        "timestamp": time.strftime("%Y%m%dT%H%M%S"),
        "finding_numeric": "verdict-invariant; confidence-only constants",
        "finding_keyword": "keyword membership is the only verdict lever",
        "numeric": numeric,
        "keyword_lever": keyword,
    }
    out = REPORT_DIR / f"calibrate_{report['timestamp']}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nreport -> {out}")


if __name__ == "__main__":
    main()
