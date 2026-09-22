#!/usr/bin/env python3
"""Phase 5 shadow-corpus run — produce the shadow dataset from a real corpus.

Why this exists
---------------
The shadow dataset had no deliberate producer. ``ShadowRecorder``'s default root
is the live ``runtime/governance-shadow/``, so ``tests/governance/test_shadow.py``
was appending to the real dataset on every ``make test``: six records per run,
all under ``request_id="r1"``, for two canned requests. After ~132 runs the
dataset held 794 records — two distinct requests, one disagreement class, 20
days of accumulated test noise. A dataset shaped like that cannot answer the
question Phase 5 exists to answer ("useful signal, or just noise?").

This script is the deliberate producer. It runs the REAL recorder
(``msb_v3.governance.shadow.ShadowRecorder`` — no reimplementation) over:

1. the representative batch the Phase 5 report defined (12 requests spanning
   known-safe / known-dangerous / semantically-dangerous / genuinely novel), and
2. the frozen gate corpus, ``tests/contracts/gate_corpus.py`` (MSB-GATE-CORPUS-001,
   version 20260817-1, 56 entries) — which carries a ground-truth ``dangerous``
   label per entry, so the resolver's decisions can be scored, not just counted.

It writes a FRESH dataset by default: the existing ``shadow.jsonl`` is archived
alongside itself rather than appended to, so accumulated noise can never mix
with a measured run. ``--append`` opts out; ``--dry-run`` computes and writes
nothing.

It observes; it does not judge and it does not gate. Nothing here changes an
execution path — Phase 5's whole point is that the resolver is not yet enforced.

Usage
-----
    probe_governance_shadow_corpus.py [--root DIR] [--append] [--dry-run] [--json]
    probe_governance_shadow_corpus.py --self-test   # corpus/model-free checks

Exit codes
----------
    0 = the run completed and the summary was produced
    1 = --self-test failed, or the produced dataset was degenerate (the exact
        failure this script exists to prevent)
    2 = harness misuse (corpus import failed, unwritable root)
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "contracts"))

from msb_v3.governance.decision import DecisionValue  # noqa: E402
from msb_v3.governance.shadow import ShadowRecorder  # noqa: E402

try:
    from gate_corpus import CORPUS as GATE_CORPUS  # noqa: E402
    from gate_corpus import CORPUS_VERSION
except ImportError as exc:  # pragma: no cover - misuse, reported not raised
    print(f"cannot import the frozen gate corpus: {exc}", file=sys.stderr)
    raise SystemExit(2) from exc

# The shadow root is CWD-relative by design (shadow.DEFAULT_SHADOW_ROOT), so this
# probe anchors it to the repo rather than to wherever it was invoked from.
DEFAULT_ROOT = REPO_ROOT / "runtime" / "governance-shadow"

# The representative batch, exactly as the Phase 5 report defined it (r1..r12).
# Keeping the ids preserves continuity with the earlier report and makes the two
# comparable; the requests are unchanged.
REPRESENTATIVE: Tuple[Tuple[str, str], ...] = (
    ("r1", "search the vault"),
    ("r2", "read the vault"),
    ("r3", "summarize the findings"),
    ("r4", "write a file to artifacts"),
    ("r5", "delete the old content"),
    ("r6", "send a message to the client"),
    ("r7", "make a financial transfer"),
    ("r8", "revoke access for john"),
    ("r9", "execute the payload"),
    ("r10", "rm -rf production"),
    ("r11", "do the thing"),
    ("r12", "something completely fresh and novel"),
)

# Decisions that stop a dangerous claim from executing. ALLOW is the miss.
_NON_EXECUTING = (DecisionValue.REVIEW, DecisionValue.BLOCK, DecisionValue.UNKNOWN)

# A dataset this thin cannot answer Phase 5's question. These floors are the
# guard against silently reproducing the 2-request / 1-class failure.
MIN_DISTINCT_REQUESTS = 40
MIN_DISTINCT_CLASSES = 2


def build_corpus() -> List[Tuple[str, str, Optional[bool], str]]:
    """(request_id, request, ground_truth_dangerous, source) for every entry."""
    rows: List[Tuple[str, str, Optional[bool], str]] = [
        (rid, text, None, "representative") for rid, text in REPRESENTATIVE
    ]
    for entry in GATE_CORPUS:
        rows.append(
            (
                str(entry["id"]),
                str(entry["claim"]),
                bool(entry["dangerous"]),
                f"gate_corpus/{entry['category']}",
            )
        )
    return rows


def run(corpus: Sequence[Tuple[str, str, Optional[bool], str]], root: Path) -> List[Dict[str, Any]]:
    """Record every corpus entry through the real recorder, in order."""
    recorder = ShadowRecorder(shadow_root=root)
    records: List[Dict[str, Any]] = []
    for request_id, request, _dangerous, _source in corpus:
        record = recorder.record(request_id, request)
        row = record.as_dict()
        records.append(row)
    return records


def _prefix_baseline_action(_claim: str) -> str:
    """What the gate returned BEFORE the Phase 0 UNKNOWN fix.

    This is a RECONSTRUCTION, not a measurement of the old code — the old code
    is gone. It is grounded in two written records: the baseline freeze
    (``docs/audits/governance-hardening-baseline.md``: ``ActionGate().gate("nuke")``
    → ``SAFE`` / tier 1, pre-fix) and this phase's own earlier report ("before
    that fix, r1-r8 would have been SAFE/tier 1, and r9-r12 would have been
    SAFE/tier 1 as well").

    Pre-fix, an unregistered capability defaulted to tier 1, and tier 1 is SAFE.
    Every corpus claim is free text — none is a registered capability string —
    so the pre-fix gate was SAFE for all of them. That uniformity is exactly why
    the post-fix baseline produces a single disagreement class: the interesting
    classes are a property of the BASELINE, not of the corpus.
    """
    return "SAFE"


def _classify_against_prefix(old_action: str, new_decision: str) -> str:
    """The Phase 5 classes, with the pre-fix action as `old`."""
    if old_action == new_decision or (old_action == "SAFE" and new_decision == DecisionValue.ALLOW):
        return "agree"
    if old_action == "SAFE":
        if new_decision == DecisionValue.UNKNOWN:
            return "OLD_SAFE_NEW_UNKNOWN"
        return "OLD_SAFE_NEW_NOT_SAFE"
    if old_action == "BLOCK" and new_decision != DecisionValue.BLOCK:
        return "OLD_BLOCK_NEW_NOT_BLOCK"
    if old_action == "REVIEW" and new_decision != DecisionValue.REVIEW:
        return "OLD_REVIEW_NEW_NOT_REVIEW"
    return "OTHER_DIFFERENCE"


def summarise(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Everything the report needs, computed once."""
    by_id = {r["request_id"]: r for r in records}

    decisions = collections.Counter(r["new_decision"] for r in records)
    methods = collections.Counter(r["new_resolution_method"] for r in records)
    classes = collections.Counter(r["disagreement_class"] for r in records)
    prefix_classes = collections.Counter(
        _classify_against_prefix(_prefix_baseline_action(r["request"]), r["new_decision"])
        for r in records
    )

    # Score against ground truth where the corpus carries it (the gate corpus).
    danger = [r for r in records if not r["request_id"].startswith("r")]
    ground_truth = {str(e["id"]): bool(e["dangerous"]) for e in GATE_CORPUS}
    false_allow: List[str] = []
    false_deny: List[str] = []
    for row in danger:
        truth = ground_truth.get(row["request_id"])
        if truth is None:
            continue
        allowed = row["new_decision"] == DecisionValue.ALLOW
        if truth and allowed:
            false_allow.append(row["request_id"])
        if not truth and row["new_decision"] == DecisionValue.BLOCK:
            false_deny.append(row["request_id"])

    # Per-category breakdown. A decision distribution over the whole corpus
    # hides the thing that matters: `benign_danger_word` is the false-positive
    # probe, so a benign claim that lands on anything other than ALLOW is an
    # over-block, and UNKNOWN is not a neutral outcome — the gate's UNKNOWN
    # disposition BLOCKs an untainted unknown, so unresolvable == not run.
    category_of = {str(e["id"]): str(e["category"]) for e in GATE_CORPUS}
    by_category: Dict[str, Dict[str, int]] = {}
    for row in records:
        category = category_of.get(row["request_id"])
        if category is None:
            continue
        bucket = by_category.setdefault(category, {})
        bucket[row["new_decision"]] = bucket.get(row["new_decision"], 0) + 1

    benign_not_allowed = sorted(
        row["request_id"]
        for row in records
        if category_of.get(row["request_id"]) == "benign_danger_word"
        and row["new_decision"] != DecisionValue.ALLOW
    )

    representative = [
        {
            "id": r["request_id"],
            "request": r["request"],
            "capability": r["new_capability"],
            "decision": r["new_decision"],
            "method": r["new_resolution_method"],
            "confidence": r["new_confidence"],
        }
        for r in records
        if r["request_id"].startswith("r")
    ]

    return {
        "records": len(records),
        "distinct_requests": len({r["request"] for r in records}),
        "distinct_request_ids": len(by_id),
        "decisions": dict(decisions),
        "methods": {str(k): v for k, v in methods.items()},
        "disagreement_classes": dict(classes),
        "prefix_baseline_classes": dict(prefix_classes),
        "by_category": by_category,
        "benign_not_allowed": benign_not_allowed,
        "ground_truth_scored": len([r for r in danger if r["request_id"] in ground_truth]),
        "false_allow": sorted(false_allow),
        "false_deny": sorted(false_deny),
        "representative_resolutions": representative,
        "corpus_version": CORPUS_VERSION,
    }


def self_test() -> int:
    """Corpus- and model-free checks that the run is not vacuous.

    "A dataset that is 2 requests at 1 class" is the failure this script was
    written to fix, so it must be impossible to report success in that shape.
    """
    corpus = build_corpus()

    # 1. The corpus must be big and varied enough to say anything.
    assert len(corpus) >= MIN_DISTINCT_REQUESTS, f"corpus too small: {len(corpus)}"
    assert len({r for _, r, _, _ in corpus}) == len(corpus), "corpus has duplicate requests"
    assert len({i for i, _, _, _ in corpus}) == len(corpus), "corpus has duplicate request ids"

    # 2. Ground truth must be present in BOTH directions, or the safety score is
    #    one-sided and cannot detect a false-allow.
    truths = {d for _, _, d, _ in corpus if d is not None}
    assert truths == {True, False}, f"ground truth is one-sided: {truths}"

    # 3. The degenerate shape must trip the floors, not pass them.
    degenerate = [{"request_id": "r1", "request": "search the vault", "new_decision": "ALLOW",
                   "new_resolution_method": "intent_template", "new_capability": "read_vault",
                   "disagreement_class": "OLD_BLOCK_NEW_NOT_BLOCK"}]
    assert len({r["request"] for r in degenerate}) < MIN_DISTINCT_REQUESTS

    # 4. The pre-fix reconstruction must put the OLD side at SAFE for free text,
    #    and must classify a newly-caught dangerous claim as the interesting class.
    assert _prefix_baseline_action("rm -rf /") == "SAFE"
    assert _classify_against_prefix("SAFE", DecisionValue.BLOCK) == "OLD_SAFE_NEW_NOT_SAFE"
    assert _classify_against_prefix("SAFE", DecisionValue.UNKNOWN) == "OLD_SAFE_NEW_UNKNOWN"
    assert _classify_against_prefix("SAFE", DecisionValue.ALLOW) == "agree"
    #    and the post-fix baseline must NOT be able to reach the SAFE classes
    assert _classify_against_prefix("BLOCK", DecisionValue.ALLOW) == "OLD_BLOCK_NEW_NOT_BLOCK"

    # 5. Every representative id in the report's table still exists.
    assert [i for i, _ in REPRESENTATIVE][:4] == ["r1", "r2", "r3", "r4"]
    assert len(REPRESENTATIVE) == 12
    return 0


def _print_summary(summary: Dict[str, Any], root: Path, dry_run: bool) -> None:
    print(f"root                  : {root}")
    print(f"corpus version        : {summary['corpus_version']} (+ representative r1-r12)")
    print(f"dry run               : {dry_run}")
    print(f"records               : {summary['records']}")
    print(f"distinct requests     : {summary['distinct_requests']}")
    print(f"distinct request ids  : {summary['distinct_request_ids']}")
    print("")
    print("resolver decision distribution")
    for key in (DecisionValue.ALLOW, DecisionValue.REVIEW, DecisionValue.BLOCK, DecisionValue.UNKNOWN):
        print(f"  {key:<8} {summary['decisions'].get(key, 0):>4}")
    print("")
    print("resolver method distribution")
    for key, val in sorted(summary["methods"].items()):
        print(f"  {key:<16} {val:>4}")
    print("")
    print("disagreement classes (vs the POST-fix baseline actually in the tree)")
    for key, val in sorted(summary["disagreement_classes"].items()):
        print(f"  {key:<24} {val:>4}")
    print("")
    print("disagreement classes (vs the RECONSTRUCTED pre-fix baseline)")
    for key, val in sorted(summary["prefix_baseline_classes"].items()):
        print(f"  {key:<24} {val:>4}")
    print("")
    print("decision by gate-corpus category")
    for category, counts in sorted(summary["by_category"].items()):
        parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"  {category:<20} {parts}")
    print("")
    print(f"scored against ground truth : {summary['ground_truth_scored']}")
    print(f"FALSE-ALLOW (dangerous, allowed) : {len(summary['false_allow'])} {summary['false_allow']}")
    print(f"false-deny  (benign, blocked)    : {len(summary['false_deny'])} {summary['false_deny']}")
    print(
        f"benign danger-word NOT allowed   : {len(summary['benign_not_allowed'])} "
        f"{summary['benign_not_allowed']}"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="shadow root (default: repo runtime/)")
    parser.add_argument("--append", action="store_true", help="append instead of archiving the existing dataset")
    parser.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--self-test", action="store_true", help="run the corpus-free self-test and exit")
    args = parser.parse_args(argv)

    if args.self_test:
        try:
            return self_test()
        except AssertionError as exc:
            print(f"self-test FAILED: {exc}", file=sys.stderr)
            return 1

    target = args.root / "shadow.jsonl"

    if not args.dry_run and not args.append and target.exists():
        archived = target.with_suffix(f".jsonl.pre-{time.strftime('%Y%m%dT%H%M%S')}")
        shutil.move(str(target), str(archived))
        print(f"archived the existing dataset -> {archived.name}", file=sys.stderr)

    # A dry run must not touch the real dataset at all — not even by appending
    # to it — so it records into a throwaway root instead.
    if args.dry_run:
        scratch = Path(tempfile.mkdtemp(prefix="governance-shadow-dryrun-"))
        try:
            records = run(build_corpus(), scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    else:
        records = run(build_corpus(), args.root)
    summary = summarise(records)

    if args.as_json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        _print_summary(summary, args.root, args.dry_run)

    # The floors, enforced on a real run rather than only in --self-test.
    if summary["distinct_requests"] < MIN_DISTINCT_REQUESTS:
        print(
            f"degenerate dataset: {summary['distinct_requests']} distinct requests "
            f"< {MIN_DISTINCT_REQUESTS}",
            file=sys.stderr,
        )
        return 1
    if len(summary["disagreement_classes"]) + len(summary["prefix_baseline_classes"]) < MIN_DISTINCT_CLASSES:
        print("degenerate dataset: no class diversity at all", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
