"""``python -m msb_v3.steward`` — canonical project-state CLI.

Commands:
    validate <state.json>
        Parse + validate a project-state document against the §7/§53/§54
        contract.  Exit 0 when valid, 1 with every issue when not — the
        authoring hook that keeps the state file honest.
    health <state.json>
        Print the §53 health vector table (axis, value, evidence).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .state import load_state


def cmd_validate(args: argparse.Namespace) -> int:
    state = load_state(args.path)
    if state.valid:
        print(f"VALID: {args.path} conforms to {state.data.get('schema')}")
        return 0
    print(f"INVALID: {args.path}")
    for issue in state.issues:
        print(f"  - {issue}")
    return 1


def cmd_health(args: argparse.Namespace) -> int:
    state = load_state(args.path)
    if not state.valid:
        print(f"INVALID: {args.path} — fix before reading health")
        for issue in state.issues:
            print(f"  - {issue}")
        return 1
    print(state.health_table())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m msb_v3.steward",
        description="Canonical project state (AIL–MoIE Steward, Layer 02).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate a state file")
    p_validate.add_argument(
        "path",
        type=lambda s: Path(s).expanduser(),
        help="path to project-state.json",
    )
    p_validate.set_defaults(func=cmd_validate)

    p_health = sub.add_parser(
        "health", help="print the §53 health vector"
    )
    p_health.add_argument(
        "path",
        type=lambda s: Path(s).expanduser(),
        help="path to project-state.json",
    )
    p_health.set_defaults(func=cmd_health)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())