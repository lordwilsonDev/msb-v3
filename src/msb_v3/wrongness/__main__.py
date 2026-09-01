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
    run-all <claims-dir> [--repo ROOT] [--out DIR]
        Run every claim JSON in a directory (underscore-prefixed files
        skipped — the _TEMPLATE.json convention) and print a verdict table.
        With --out, write a per-claim markdown report into DIR (M8: the
        vault claims home is run in one command).
    validate <claim.json>
        Parse a claim against the schema and report validity — the
        authoring hook that catches malformed claims before they run.
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


def cmd_run_all(args: argparse.Namespace) -> int:
    import json as _json

    base = Path(args.claims_dir)
    if not base.is_dir():
        print(f"error: claims dir {args.claims_dir!r} not found")
        return 1
    files = sorted(f for f in base.glob("*.json") if not f.name.startswith("_"))
    if not files:
        print(f"no claim JSON files in {base} (underscore-prefixed files are skipped)")
        return 0
    engine = WrongnessEngine(args.repo)
    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    print(f"{'claim':<28} {'verdict':<12} {'urgency':<8} checks")
    print("-" * 62)
    for f in files:
        claim = Claim.from_dict(_json.loads(f.read_text(encoding="utf-8")))
        result = engine.run(claim)
        print(f"{claim.id[:27]:<28} {result.verdict:<12} {result.urgency:<8.2f} {len(result.checks)}")
        if out_dir:
            (out_dir / f"{claim.id}.md").write_text(
                render_report(result, repo_root=args.repo), encoding="utf-8"
            )
    if out_dir:
        print(f"reports written to {out_dir}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    import json as _json

    try:
        data = _json.loads(Path(args.claim).read_text(encoding="utf-8"))
        claim = Claim.from_dict(data)
    except Exception as exc:
        print(f"invalid claim {args.claim}: {exc}")
        return 1
    print(f"valid claim: {claim.id} — {claim.statement}")
    print(f"  checks: {len(claim.checks)}, consequence: {claim.consequence}")
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

    p_run_all = sub.add_parser("run-all", help="run every claim in a directory (M8)")
    p_run_all.add_argument("claims_dir")
    p_run_all.add_argument("--repo", default=".", help="repo root for deterministic checks")
    p_run_all.add_argument("--out", default=None, help="write per-claim markdown reports here")
    p_run_all.set_defaults(func=cmd_run_all)

    p_validate = sub.add_parser("validate", help="check a claim against the schema")
    p_validate.add_argument("claim")
    p_validate.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
