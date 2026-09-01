"""``python -m msb_v3.wrongness`` — Wrongness Engine MVP CLI.

Commands:
    run <claim.json> [--repo ROOT] [--out out.json]
        Run one live claim: execute its deterministic checks, run all passes,
        print the verdict + findings.
    replay [--repo ROOT] [--corpus corpus.json] [--blind] [--held-out]
        Replay the by-hand 21-decision corpus and print §VII scores.
        --blind     disable recorded routing (what the machinery discovers
                    without the author's annotations — M3)
        --held-out  split the corpus in two and score each half
        (the decision must survive on both halves)
    score <claim.json> [--repo ROOT]
        Run a claim and print its verdict + urgency.
    report <claim.json> [--repo ROOT] [--out report.md]
        Run a claim and render the human read-path (M7): findings grouped
        by tier, evidence links (M6), and an investigation path for every
        CHECK finding.  Write the markdown when --out is given.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .claims import Claim
from .engine import (
    WrongnessEngine,
    load_corpus,
    run_replay,
    save_result,
    split_held_out,
)
from .report import render_report

_DEFAULT_CORPUS = Path(__file__).parent / "corpus" / "byhand_21.json"


def _load_claim(path: str) -> Claim:
    import json

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Claim.from_dict(data)


def cmd_run(args: argparse.Namespace) -> int:
    claim = _load_claim(args.claim)
    engine = WrongnessEngine(args.repo)
    result = engine.run(claim)
    print(f"claim      : {claim.id}")
    print(f"statement  : {claim.statement}")
    print(f"verdict    : {result.verdict}")
    print(f"urgency    : {result.urgency:.3f} (consequence={claim.consequence})")
    for res in result.checks:
        print(f"check      : ok={res.ok} — {res.evidence}")
    for f in result.findings:
        print(f"  [{f.tier:11s}] {f.pass_name}: {f.statement}")
    if args.out:
        save_result(result, args.out)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    corpus_path = Path(args.corpus) if args.corpus else _DEFAULT_CORPUS
    corpus = load_corpus(corpus_path)
    if args.held_out:
        halves = split_held_out(corpus)
        for k, half in enumerate(halves, start=1):
            score = run_replay(half, repo_root=args.repo, use_recorded_routing=not args.blind)
            print(f"half {k}       : {len(half)} claims")
            print(f"  PEDR       : {score.pedr:.3f} ({score.predicted_failures}/{score.actual_failures})")
            print(f"  FP strict  : {score.fp_rate_strict:.3f} ({score.false_positives_strict} FPs)")
            print(f"  FP assert  : {score.fp_rate_assertion:.3f} ({score.false_positives_assertion} FPs)")
            print(f"  decision   : {score.decision}")
        return 0
    score = run_replay(corpus, repo_root=args.repo, use_recorded_routing=not args.blind)
    mode = "blind (recorded routing disabled)" if args.blind else "recorded routing"
    print(f"corpus     : {corpus_path} ({len(corpus)} claims, {mode})")
    print(f"PEDR       : {score.pedr:.3f} ({score.predicted_failures}/{score.actual_failures})")
    print(f"FP strict  : {score.fp_rate_strict:.3f} ({score.false_positives_strict} FPs)")
    print(f"FP assert  : {score.fp_rate_assertion:.3f} ({score.false_positives_assertion} FPs)")
    print(f"decision   : {score.decision}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    claim = _load_claim(args.claim)
    engine = WrongnessEngine(args.repo)
    result = engine.run(claim)
    text = render_report(result, repo_root=args.repo)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"report written to {args.out}")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    claim = _load_claim(args.claim)
    engine = WrongnessEngine(args.repo)
    result = engine.run(claim)
    print(f"claim      : {claim.id}")
    print(f"statement  : {claim.statement}")
    print(f"verdict    : {result.verdict}")
    print(f"urgency    : {result.urgency:.3f} (consequence={claim.consequence})")
    for res in result.checks:
        print(f"check      : ok={res.ok} — {res.evidence}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m msb_v3.wrongness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run one live claim")
    p_run.add_argument("claim")
    p_run.add_argument("--repo", default=".", help="repo root for deterministic checks")
    p_run.add_argument("--out", default=None, help="write result JSON here")
    p_run.set_defaults(func=cmd_run)

    p_replay = sub.add_parser("replay", help="replay the by-hand corpus")
    p_replay.add_argument("--repo", default=None, help="repo root for live checks (C4/C5/C6)")
    p_replay.add_argument("--corpus", default=None, help="corpus JSON (default: byhand_21.json)")
    p_replay.add_argument("--blind", action="store_true", help="disable recorded routing (M3)")
    p_replay.add_argument("--held-out", action="store_true", help="score each half separately")
    p_replay.set_defaults(func=cmd_replay)

    p_score = sub.add_parser("score", help="run a claim and classify it")
    p_score.add_argument("claim")
    p_score.add_argument("--repo", default=".")
    p_score.set_defaults(func=cmd_score)

    p_report = sub.add_parser("report", help="render the human read-path for a claim (M7)")
    p_report.add_argument("claim")
    p_report.add_argument("--repo", default=".")
    p_report.add_argument("--out", default=None, help="write markdown here")
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
