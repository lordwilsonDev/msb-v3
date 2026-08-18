"""CLI: python -m msb_v3.moie policy [--policy PATH] [--json] [--strict]

The detection-policy surface (MSB-CAL-006/007). One command, two jobs:

1. VALIDATE config/risk_templates.json with the same fail-closed loader the
   engine uses at import — missing/corrupt/malformed/incomplete policy
   exits 1 with the reason, before any expert is touched.
2. DIFF the policy's detection coverage against the frozen gate corpus
   (MSB-GATE-CORPUS-001): per-category blocked/total with miss ids,
   overall precision/recall/F1, and a comparison against the pinned
   baseline (MSB-GATE-EVAL-001, 17/8/8/23).

With ``--policy PATH``, validate + apply a CANDIDATE policy file instead
and show the coverage delta vs the committed policy (newly blocked /
newly missed ids, metric drift) — the edit-test-commit loop for keyword
tuning without touching the shipped file.

``--strict`` exits 2 when coverage drifts from the pinned baseline —
a CI hook so a policy edit that silently weakens detection fails loudly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from msb_v3.core.config import settings

# The corpus is a frozen test artifact; reuse it directly (calibrate.py
# does the same) so the CLI, the harness, and the contract tests all
# measure the SAME ground truth.
sys.path.insert(0, str(Path(settings.msb_home) / "tests" / "contracts"))

from gate_corpus import CORPUS, CORPUS_VERSION  # type: ignore[import-not-found]  # noqa: E402,I001

# Pinned baseline from MSB-GATE-EVAL-001 (20260817), also enforced by
# tests/contracts/test_gate_contract.py::test_gate_precision_recall_pinned.
PINNED_BASELINE: Dict[str, int] = {"tp": 17, "fp": 8, "tn": 8, "fn": 23}


def _policy_version(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data.get("version", "?"))
    except Exception:  # noqa: BLE001 - version is cosmetic; validation is the real gate
        return "?"


def _measure() -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]], Dict[str, bool]]:
    """Run the gate over the corpus: (metrics, per-category, verdict map)."""
    from msb_v3.moie import MoIEController

    # A controller with the evidence seam disabled — the gate contract
    # measures the keyword pre-filter, not the memory-fabric recall layer.
    controller = MoIEController(retriever=lambda _claim: [])
    tp = fp = tn = fn = 0
    categories: Dict[str, Dict[str, Any]] = {}
    verdicts: Dict[str, bool] = {}
    for entry in CORPUS:
        eid = str(entry["id"])
        blocked = controller.analyze(str(entry["claim"])).verdict == "BLOCK"
        verdicts[eid] = blocked
        dangerous = bool(entry["dangerous"])
        cat = str(entry["category"])
        bucket = categories.setdefault(cat, {"blocked": [], "total": 0})
        bucket["total"] += 1
        if dangerous and blocked:
            tp += 1
        elif dangerous and not blocked:
            fn += 1
        elif not dangerous and blocked:
            fp += 1
            bucket["blocked"].append(eid)
        else:
            tn += 1
        if dangerous and blocked:
            bucket["blocked"].append(eid)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = 2 * precision * recall / (precision + recall) if (precision and recall) else None
    metrics = {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
    }
    return metrics, categories, verdicts


def _diff_verdicts(base: Dict[str, bool], cand: Dict[str, bool]) -> Dict[str, List[str]]:
    """Ids whose BLOCK status changed between two policies."""
    return {
        "newly_blocked": sorted(eid for eid in cand if cand[eid] and not base.get(eid)),
        "newly_missed": sorted(eid for eid in base if base.get(eid) and not cand.get(eid)),
    }


def _fmt_metrics(m: Dict[str, Any]) -> str:
    return (
        f"precision {m['precision']}  recall {m['recall']}  f1 {m['f1']}  "
        f"(tp={m['tp']} fp={m['fp']} tn={m['tn']} fn={m['fn']})"
    )


def _render_human(path: Path, metrics: Dict[str, Any], categories: Dict[str, Dict[str, Any]],
                  match: bool, diff: Optional[Dict[str, List[str]]] = None) -> None:
    print(f"[moie] policy {path} v{_policy_version(path)}")
    print(f"[moie] coverage vs MSB-GATE-CORPUS-001 ({CORPUS_VERSION}, {len(CORPUS)} claims):")
    order = ["dangerous", "benign_danger_word", "ambiguous", "obfuscated", "encoded", "multilingual"]
    dangerous_ids = {str(e["id"]) for e in CORPUS if e["dangerous"]}
    for cat in order:
        if cat not in categories:
            continue
        b = categories[cat]
        blocked_ids = set(b["blocked"])
        miss_ids = sorted((dangerous_ids & {str(e["id"]) for e in CORPUS if e["category"] == cat}) - blocked_ids)
        extra = ""
        if cat == "benign_danger_word" and blocked_ids:
            extra = "   false positives: " + " ".join(sorted(blocked_ids))
        elif miss_ids:
            extra = "   misses: " + " ".join(miss_ids)
        print(f"  {cat:<18} {len(blocked_ids):>2}/{b['total']:<2} blocked{extra}")
    print(f"[moie] {_fmt_metrics(metrics)}")
    status = "MATCH" if match else "DRIFT"
    print(f"[moie] baseline MSB-GATE-EVAL-001: {status}")
    if diff:
        print("[moie] diff vs committed policy:")
        print("  + blocked: " + (" ".join(diff["newly_blocked"]) if diff["newly_blocked"] else "(none)"))
        print("  - missed:  " + (" ".join(diff["newly_missed"]) if diff["newly_missed"] else "(none)"))


def cmd_policy(args: argparse.Namespace) -> int:
    # msb_v3.moie is imported LAZILY and guarded: the module-level policy
    # load honors MSB_RISK_POLICY_PATH (fail-closed for the engine — a
    # corrupt policy env must prevent boot), so importing it can itself
    # raise. For the CLI that is a validation failure to report cleanly,
    # not a traceback.
    try:
        from msb_v3.moie.experts import apply_policy_overrides, risk_policy_path
    except RuntimeError as exc:
        print(f"[moie] validation FAILED: {exc}", file=sys.stderr)
        return 1

    # The committed policy is ALWAYS the repo's config/risk_templates.json —
    # MSB_RISK_POLICY_PATH only redirects the candidate, so a CI gate or
    # operator can diff a candidate against the true committed baseline.
    committed = Path(settings.msb_home) / "config" / "risk_templates.json"
    candidate = Path(args.policy).expanduser() if args.policy else risk_policy_path()

    # 1. Validate with the fail-closed loader (atomic: raises before mutation).
    try:
        apply_policy_overrides(candidate)
    except RuntimeError as exc:
        print(f"[moie] validation FAILED: {exc}", file=sys.stderr)
        return 1

    metrics, categories, verdicts = _measure()
    match = {k: metrics[k] for k in ("tp", "fp", "tn", "fn")} == PINNED_BASELINE

    diff = None
    if candidate != committed:
        # Baseline: re-apply the committed policy and measure it.
        apply_policy_overrides(committed)
        _, _, base_verdicts = _measure()
        diff = _diff_verdicts(base_verdicts, verdicts)
        # Restore the candidate so the running process matches what we validated.
        apply_policy_overrides(candidate)

    if args.json:
        payload: Dict[str, Any] = {
            "policy": {"path": str(candidate), "version": _policy_version(candidate), "valid": True},
            "corpus": {"version": CORPUS_VERSION, "total": len(CORPUS)},
            "coverage": metrics,
            "categories": {
                cat: {"blocked": len(b["blocked"]), "total": b["total"], "ids": sorted(b["blocked"])}
                for cat, b in categories.items()
            },
            "baseline": {"pinned": PINNED_BASELINE, "match": match},
            "diff": diff,
        }
        print(json.dumps(payload, indent=2))
    else:
        _render_human(candidate, metrics, categories, match, diff)

    if args.strict and not match:
        print("[moie] strict: coverage drifted from pinned baseline — failing", file=sys.stderr)
        return 2
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="msb_v3.moie")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser(
        "policy",
        help="validate the detection policy and diff its coverage against the gate corpus",
    )
    p.add_argument(
        "--policy",
        default="",
        help="candidate policy file to validate + diff against the committed policy",
    )
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument(
        "--strict",
        action="store_true",
        help="exit 2 when coverage drifts from the pinned baseline (CI hook)",
    )

    args = ap.parse_args(argv)
    return {"policy": cmd_policy}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
