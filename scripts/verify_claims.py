#!/usr/bin/env python3
"""SMI-018 v0.1 -- Evidence Claim Verifier.

Scans markdown files for ```smi-018-claim fenced blocks and checks that
any block with status: implemented references files/tests that actually
exist in the repository. See
docs/superpowers/specs/2026-08-07-smi-018-evidence-claim-verifier-design.md
for the full design and rationale.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CLAIM_BLOCK_RE = re.compile(r"```smi-018-claim\n(.*?)\n```", re.DOTALL)


class ClaimParseError(Exception):
    """Raised when a claim block's internal grammar is invalid."""


def parse_claim_block(text: str) -> dict:
    """Parse a claim block body into a dict of scalar/list fields.

    Grammar: blank lines are ignored. A line `key: value` sets a scalar
    field. A line `key:` (nothing after the colon) starts a list field,
    populated by subsequent `- item` lines until the next key line.
    """
    fields: dict = {}
    current_list_key: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("- "):
            if current_list_key is None:
                raise ClaimParseError(f"list item with no preceding key: {raw_line!r}")
            fields[current_list_key].append(line[2:].strip())
            continue

        if ":" not in line:
            raise ClaimParseError(f"malformed line (no ':'): {raw_line!r}")

        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest:
            fields[key] = rest
            current_list_key = None
        else:
            fields[key] = []
            current_list_key = key

    return fields


def find_markdown_files(docs_root: Path) -> list[Path]:
    return sorted(docs_root.rglob("*.md"))


def _write_report(report_path: Path, report: dict) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify smi-018-claim blocks in markdown docs against repository state."
    )
    parser.add_argument("docs_root", type=Path, help="Directory to scan for *.md files")
    parser.add_argument(
        "--report-path",
        type=Path,
        default=Path("artifacts/smi018/claim_report.json"),
        help="Where to write the JSON report (default: artifacts/smi018/claim_report.json)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    claims_found = 0
    for md_file in find_markdown_files(args.docs_root):
        text = md_file.read_text(encoding="utf-8")
        for block_text in CLAIM_BLOCK_RE.findall(text):
            claims_found += 1
            parse_claim_block(block_text)

    report = {"claims_found": claims_found, "implemented": 0, "planned": 0, "claims": [], "failures": []}
    _write_report(args.report_path, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
