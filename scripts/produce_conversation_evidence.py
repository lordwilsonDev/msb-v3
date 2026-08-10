#!/usr/bin/env python3
"""Conversation → ledger evidence producer CLI.

Mirrors sovereign-verification/scripts/replay_feedback_events.py: replays the
conversation record stream (`<ledger>/records/conversation.jsonl`) into the
sovereign-verification ledger — idempotent, content-addressed, fail-loud.

Usage:
    produce_conversation_evidence.py --records <stream.jsonl> --ledger-dir DIR
        [--git-head SHA] [--dry-run] [--self-test]

Exit codes: 0 = ok / no records / nothing new; 1 = malformed stream or
rejected record (a ledger incident); 2 = usage error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from msb_v3.conversation import producer  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--records", type=Path, help="conversation record stream (.jsonl)")
    parser.add_argument("--ledger-dir", type=Path, default=None,
                        help="ledger dir (default: MSB_CONVERSATION_LEDGER_DIR or the shared constellation ledger)")
    parser.add_argument("--git-head", default=None, help="repo identity at log time")
    parser.add_argument("--dry-run", action="store_true", help="compute refs + claims state without writing")
    parser.add_argument("--self-test", action="store_true", help="run the zero-spend in-memory self test")
    args = parser.parse_args(argv)

    if args.self_test:
        try:
            rc = producer.run_self_test()
        except AssertionError as exc:
            print(f"self-test FAILED: {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"self-test ERROR: {exc}")
            return 1
        print("self-test PASS (artifact shape, polarity mapping, idempotency, prohibited transitions)")
        return rc

    if not args.records:
        parser.error("--records is required (or use --self-test)")

    ledger_dir = args.ledger_dir or producer.default_ledger_dir()
    git_head = args.git_head or producer.default_git_head()

    summary = producer.ingest_stream(args.records, ledger_dir, git_head, dry_run=args.dry_run)
    if summary["errors"]:
        for err in summary["errors"]:
            print(f"ERROR: {err}")
        print(f"ingest FAILED ({summary['status']}): nothing ingested — stream is corrupt or rejected")
        return 1

    mode = "DRY-RUN" if args.dry_run else "INGEST"
    print(
        f"{mode} status={summary['status']} records={summary['records']} "
        f"ingested={summary['ingested']} skipped={summary['skipped']} claims={summary['claims']}"
    )
    if summary.get("verdicts"):
        print("claim verdicts:")
        for claim_id, verdict in sorted(summary["verdicts"].items()):
            print(f"  {claim_id}: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
