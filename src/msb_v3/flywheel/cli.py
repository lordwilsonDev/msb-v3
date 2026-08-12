"""CLI: python -m msb_v3.flywheel turn|status|approve|resume|show|config

The terminal surface for the flywheel (the cockpit build-mode is a later
plan). Mirrors the governance/ops CLI style.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from msb_v3.flywheel.engine import FlywheelEngine


def _engine() -> FlywheelEngine:
    return FlywheelEngine()


def _line(turn) -> str:
    extra = ""
    if turn.status == "WAITING_APPROVAL":
        pending = [s for s, i in turn.approval_ids.items()]
        extra = f"  -> awaiting approval at: {', '.join(pending)}"
    elif turn.status == "DONE":
        extra = f"  -> recorded at {turn.record_path or '—'}"
    elif turn.notes:
        extra = f"  ({turn.notes[-1]})"
    return f"[flywheel] {turn.turn_id}  {turn.status:16s} {turn.stage:18s} {turn.problem[:60]}{extra}"


def cmd_turn(args: argparse.Namespace) -> int:
    engine = _engine()
    turn = engine.start(args.problem, charger=args.charger, skill=args.skill)
    if turn.status == "BLOCKED":
        print(f"[flywheel] start blocked: {turn.notes[-1]}")
        return 1
    turn = engine.run(turn.turn_id)
    print(_line(turn))
    if turn.status == "WAITING_APPROVAL":
        print(f"[flywheel] approve with: python -m msb_v3.flywheel approve {turn.turn_id}")
    elif turn.status == "HALTED":
        print(f"[flywheel] halted: {turn.notes[-1]}")
        return 1
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    engine = _engine()
    turns = engine.list()
    if not turns:
        print("[flywheel] no turns yet — start one with: python -m msb_v3.flywheel turn \"<problem>\"")
        return 0
    print(f"[flywheel] {len(turns)} turn(s):")
    for t in turns[:10]:
        print(" ", _line(t))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    engine = _engine()
    turn = engine.get(args.turn_id)
    if turn is None:
        print(f"[flywheel] ERROR: unknown turn {args.turn_id}", file=sys.stderr)
        return 1
    print(_line(turn))
    for note in turn.notes:
        print(f"  - {note}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    engine = _engine()
    try:
        turn = engine.approve(args.turn_id, operator=args.operator)
    except ValueError as exc:
        print(f"[flywheel] ERROR: {exc}", file=sys.stderr)
        return 1
    print(_line(turn))
    if turn.status == "WAITING_APPROVAL":
        print(f"[flywheel] still awaiting approval at {turn.stage}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Print the guard/brake/approval/flywheel config — the same blocks as
    /system/config and make governance-config, from the shared builder.
    --json emits the verbatim blocks; the default is the shared
    human-readable rendering (identical to the governance console)."""
    from msb_v3.core.guard_config import guard_config, render_human

    cfg = guard_config()
    if args.json:
        print(json.dumps(cfg, indent=2))
        return 0
    sys.stdout.write(render_human(cfg))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    engine = _engine()
    try:
        turn = engine.resume(args.turn_id)
    except ValueError as exc:
        print(f"[flywheel] ERROR: {exc}", file=sys.stderr)
        return 1
    print(_line(turn))
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="msb_v3.flywheel")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("turn", help="start and advance a turn")
    t.add_argument("problem")
    t.add_argument("--charger", choices=("stub", "sovereign"), default="stub",
                   help="generative brain: stub (offline, deterministic) or sovereign (real research LLM)")
    t.add_argument("--skill", default="")

    sub.add_parser("status", help="list turns")

    s = sub.add_parser("show", help="show one turn's detail")
    s.add_argument("turn_id")

    a = sub.add_parser("approve", help="approve a turn's pending approvals and resume it")
    a.add_argument("turn_id")
    a.add_argument("--operator", default="cli")

    r = sub.add_parser("resume", help="resume a parked/halted turn")
    r.add_argument("turn_id")

    cfgp = sub.add_parser("config", help="guard/brake/approval/flywheel config (same blocks as /system/config)")
    cfgp.add_argument("--json", action="store_true", help="print the verbatim config blocks as JSON")

    args = ap.parse_args(argv)
    return {
        "turn": cmd_turn,
        "status": cmd_status,
        "show": cmd_show,
        "approve": cmd_approve,
        "resume": cmd_resume,
        "config": cmd_config,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
