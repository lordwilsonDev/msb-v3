"""CLI: python -m msb_v3.cron list | add | remove | run | history

The operator surface for scheduled jobs, mirroring the /cron REST API. A
manual ``run`` executes the same governed path as a scheduled firing (kill
switch, retries, timeout, receipts), so the CLI is safe to call from
launchd or a cron line:

    0 2 * * * cd $MSB_REPO && python -m msb_v3.cron run daily-backup

Schedules are standard 5-field cron expressions (numeric only):
    * * * * *   minute hour day-of-month month day-of-week (0-7, 0/7=Sun)
    */15 * * * *   every 15 minutes
    0 2 * * *     daily at 02:00
    0 */6 * * *   every 6 hours
    0 0 1 * 1     first of the month OR any Monday (Vixie semantics)
"""

from __future__ import annotations

import argparse
import sys

from msb_v3.cron.actions import ACTIONS
from msb_v3.cron.parser import CronExpr
from msb_v3.cron.scheduler import CronScheduler
from msb_v3.cron.store import CronStore


def _slugify(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.strip().lower()).strip("-")


def _fmt_next(schedule: str) -> str:
    try:
        nxt = CronExpr.parse(schedule).next_run()
        return nxt.strftime("%Y-%m-%d %H:%M UTC") if nxt else "-"
    except ValueError:
        return "invalid"


def _print_job(job: dict) -> None:
    print(
        f"{job['job_id']:<24} {'enabled' if job['enabled'] else 'disabled':<9} "
        f"{job['schedule']:<14} {job['action'].get('type', '?'):<20} {job['name']}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="msb_v3.cron", description="Scheduled governed jobs for msb-v3")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list all jobs")

    add = sub.add_parser("add", help="add a job")
    add.add_argument("--name", required=True, help="human-readable job name")
    add.add_argument("--job-id", default=None, help="id (default: slugified name)")
    add.add_argument("--schedule", required=True, help="5-field cron expression, e.g. '0 2 * * *'")
    add.add_argument("--action", required=True, choices=sorted(ACTIONS), help="built-in action type")
    add.add_argument("--param", action="append", default=[], metavar="KEY=VALUE", help="action param (repeatable; VALUE is JSON-decoded when possible)")
    add.add_argument("--disabled", action="store_true", help="create disabled")
    add.add_argument("--require-approval", action="store_true", help="never auto-run on schedule; operator-triggered only")
    add.add_argument("--max-retries", type=int, default=2)
    add.add_argument("--timeout", type=float, default=300.0, help="per-attempt timeout in seconds")

    rm = sub.add_parser("remove", help="remove a job")
    rm.add_argument("job_id")

    run = sub.add_parser("run", help="run a job now (governed)")
    run.add_argument("job_id")

    hist = sub.add_parser("history", help="recent runs of a job")
    hist.add_argument("job_id")
    hist.add_argument("--limit", type=int, default=10)

    args = ap.parse_args(argv)

    store = CronStore()
    scheduler = CronScheduler(store)

    if args.cmd == "list":
        jobs = store.list_jobs()
        for job in jobs:
            _print_job(job)
        print(f"\n{len(jobs)} job(s)")
        return 0

    if args.cmd == "add":
        params: dict = {}
        for item in args.param:
            key, sep, raw = item.partition("=")
            if not sep:
                print(f"[cron] ERROR: --param must be KEY=VALUE (got {item!r})", file=sys.stderr)
                return 2
            try:
                import json as _json

                params[key] = _json.loads(raw)
            except ValueError:
                params[key] = raw
        try:
            job = store.create_job(
                args.job_id or _slugify(args.name),
                args.name,
                args.schedule,
                {"type": args.action, "params": params},
                enabled=not args.disabled,
                governance={
                    "requires_approval": args.require_approval,
                    "max_retries": args.max_retries,
                    "timeout_s": args.timeout,
                },
            )
        except ValueError as exc:
            print(f"[cron] ERROR: {exc}", file=sys.stderr)
            return 2
        _print_job(job)
        print(f"next run: {_fmt_next(job['schedule'])}")
        return 0

    if args.cmd == "remove":
        try:
            store.delete_job(args.job_id)
        except KeyError as exc:
            print(f"[cron] ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"[cron] removed {args.job_id}")
        return 0

    if args.cmd == "run":
        import asyncio

        try:
            result = asyncio.run(scheduler.run_job(args.job_id, trigger="manual"))
        except ValueError as exc:
            print(f"[cron] ERROR: {exc}", file=sys.stderr)
            return 2
        status = result.get("status", "?")
        print(
            f"[cron] {args.job_id}: {status}"
            + (f" (attempts={result.get('attempts')})" if result.get("attempts") else "")
            + (f" — {result.get('reason')}" if result.get("reason") else "")
        )
        if result.get("result"):
            res = result["result"]
            print(f"       {res.get('summary', '')}")
        return 0 if status in ("SUCCESS", "SKIPPED") else 1

    if args.cmd == "history":
        runs = store.history(args.job_id, limit=args.limit)
        if not runs:
            print(f"[cron] no runs for {args.job_id}")
            return 0
        for r in runs:
            print(
                f"{r['started_at']:<28} {r['status']:<12} {r['trigger']:<9} "
                f"attempt={r['attempt']} {r.get('summary', {}).get('summary', '') or r.get('error', '')}"
            )
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
