"""CLI: python -m msb_v3.governance status|arm|disarm|approvals|approve|reject|budget|config

The terminal surface for the brakes (the Cockpit UI is Phase 1). Mirrors
the msb_v3.ops CLI style: argparse subparsers, human-readable lines.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from msb_v3.governance.approval import ApprovalError, ApprovalQueue, IdempotencyError
from msb_v3.governance.budget import BudgetLedger
from msb_v3.governance.governor import OuroborosGovernor
from msb_v3.governance.killswitch import KillSwitch


def _singletons():
    return (
        KillSwitch(),
        BudgetLedger.from_settings(),
        ApprovalQueue(),
        OuroborosGovernor.from_settings(),
    )


def _budget_line(state: dict, category: str) -> str:
    s = state[category]
    limit = s["limit"]
    if limit == -1:
        return f"[governance] budget {category}: {s['spent']}/unlimited"
    return f"[governance] budget {category}: {s['spent']}/{limit} (remaining {s['remaining']}, window {s['window_s']}s)"


def cmd_status(args: argparse.Namespace) -> int:
    switch, ledger, queue, governor = _singletons()
    st = switch.state()
    armed = "ARMED" if st["armed"] else "disarmed"
    extra = f" ({st.get('reason', '')})" if st["armed"] and st.get("reason") else ""
    print(f"[governance] killswitch: {armed}{extra}")
    for cat in ("research_calls", "tokens", "iterations"):
        print(_budget_line(ledger.state(), cat))
    pending = queue.pending()
    print(f"[governance] approvals pending: {len(pending)}")
    for it in pending:
        ev = ", ".join(it.evidence_refs) if it.evidence_refs else "—"
        print(f"  - {it.item_id}  {it.kind:18s} {it.title}  (evidence: {ev})")
    recent = governor.history()[:3]
    if recent:
        print(f"[governance] governor: {len(governor.history())} signals in history, newest novelty {recent[0]['novelty']:.2f}")
    else:
        print("[governance] governor: no signals yet")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """Print the guard/brake/approval/flywheel config — same blocks as
    /system/config, from the shared guard_config() builder. --json emits
    the verbatim blocks for scripts; the default is human-readable lines."""
    from msb_v3.core.guard_config import guard_config

    cfg = guard_config()
    if args.json:
        print(json.dumps(cfg, indent=2))
        return 0

    gov = cfg["governance"]
    window_min = gov["GOV_BUDGET_WINDOW_MIN"]
    print("[governance] budget caps per rolling window:")
    print(f"  research_calls: {gov['GOV_BUDGET_RESEARCH_CALLS']}  (window {window_min}m)")
    print(f"  tokens: {gov['GOV_BUDGET_TOKENS']}  (window {window_min}m)")
    print(f"  iterations: {gov['GOV_BUDGET_ITERATIONS']}  (window {window_min}m)")
    print(
        "[governance] governor thresholds: "
        f"stall_limit={gov['GOV_GOVERNOR_STALL_LIMIT']} "
        f"novelty_min={gov['GOV_GOVERNOR_NOVELTY_MIN']} "
        f"dup_ratio_halt={gov['GOV_GOVERNOR_DUP_RATIO_HALT']} "
        f"history={gov['GOV_GOVERNOR_HISTORY']}"
    )
    kinds = ", ".join(cfg["approvals"]["kinds_requiring_approval"])
    print(f"[governance] approval kinds: {kinds}")
    stages = ", ".join(
        f"{s}->{k}" for s, k in cfg["approvals"]["stages_requiring_approval"].items()
    )
    print(f"[governance] approval stages: {stages}")
    fw = cfg["flywheel"]
    print(f"[flywheel] stages ({len(fw['stages'])}): {', '.join(fw['stages'])}")
    print(f"[flywheel] iterations per stage: {fw['iterations_per_stage']}")
    print(f"[flywheel] research-call spenders: {', '.join(fw['research_stages'])}")
    rl = cfg["rate_limits"]
    print(
        "[rate] chat: "
        f"{rl['OPENAI_CHAT_RATE_MAX']} req / {rl['OPENAI_CHAT_RATE_WINDOW_S']}s; "
        "embed: "
        f"{rl['OPENAI_EMBED_RATE_MAX']} items / {rl['OPENAI_EMBED_RATE_WINDOW_S']}s, "
        f"max batch {rl['OPENAI_EMBED_MAX_BATCH']}"
    )
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    _switch, ledger, _queue, _governor = _singletons()
    for cat in ("research_calls", "tokens", "iterations"):
        print(_budget_line(ledger.state(), cat))
    return 0


def cmd_arm(args: argparse.Namespace) -> int:
    switch = KillSwitch()
    switch.arm(args.operator, reason=args.reason)
    print(f"[governance] kill switch ARMED by {args.operator} (reason: {args.reason or '—'})")
    return 0


def cmd_disarm(args: argparse.Namespace) -> int:
    switch = KillSwitch()
    switch.disarm(args.operator)
    print(f"[governance] kill switch disarmed by {args.operator}")
    return 0


def cmd_approvals(args: argparse.Namespace) -> int:
    _switch, _ledger, queue, _governor = _singletons()
    items = queue.list(status=args.status)
    print(f"[governance] approvals ({args.status or 'all'}): {len(items)}")
    for it in items:
        ev = ", ".join(it.evidence_refs) if it.evidence_refs else "—"
        decided = f" by {it.decided_by} ({it.reason or '—'})" if it.decided_by else ""
        print(f"  - {it.item_id}  {it.kind:18s} {it.status:10s} {it.title}  (evidence: {ev}){decided}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    _switch, _ledger, queue, _governor = _singletons()
    try:
        item = queue.approve(args.item_id, args.operator, reason=args.reason)
    except (IdempotencyError, ApprovalError) as exc:
        print(f"[governance] ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"[governance] approved {item.kind} {item.item_id} ({item.title})")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    _switch, _ledger, queue, _governor = _singletons()
    try:
        item = queue.reject(args.item_id, args.operator, reason=args.reason)
    except (IdempotencyError, ApprovalError) as exc:
        print(f"[governance] ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"[governance] rejected {item.kind} {item.item_id} ({item.title})")
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="msb_v3.governance")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="kill switch + budgets + pending approvals + governor")
    sub.add_parser("budget", help="per-category budget state")

    cfgp = sub.add_parser("config", help="guard/brake/approval/flywheel config (same blocks as /system/config)")
    cfgp.add_argument("--json", action="store_true", help="print the verbatim config blocks as JSON")

    arm = sub.add_parser("arm", help="arm the kill switch (pause the loop)")
    arm.add_argument("reason", nargs="?", default="")
    arm.add_argument("--operator", default="cli")

    disarm = sub.add_parser("disarm", help="disarm the kill switch")
    disarm.add_argument("--operator", default="cli")

    approvals = sub.add_parser("approvals", help="list approvals")
    approvals.add_argument("--status", choices=("PENDING", "APPROVED", "REJECTED", "CANCELLED"), default=None)

    appr = sub.add_parser("approve", help="approve a pending approval item")
    appr.add_argument("item_id")
    appr.add_argument("--operator", default="cli")
    appr.add_argument("--reason", default=None)

    rej = sub.add_parser("reject", help="reject a pending approval item")
    rej.add_argument("item_id")
    rej.add_argument("reason")
    rej.add_argument("--operator", default="cli")

    args = ap.parse_args(argv)
    return {
        "status": cmd_status,
        "budget": cmd_budget,
        "config": cmd_config,
        "arm": cmd_arm,
        "disarm": cmd_disarm,
        "approvals": cmd_approvals,
        "approve": cmd_approve,
        "reject": cmd_reject,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
