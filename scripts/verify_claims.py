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
import subprocess
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


def validate_claim(claim: dict) -> list[str]:
    errors: list[str] = []

    if "id" not in claim:
        errors.append("missing required field: id")
    if "status" not in claim:
        errors.append("missing required field: status")
    elif claim["status"] not in ("planned", "implemented"):
        errors.append(f"invalid status: {claim['status']!r} (must be 'planned' or 'implemented')")

    if claim.get("status") == "implemented":
        has_files = bool(claim.get("files"))
        has_tests = bool(claim.get("tests"))
        if not has_files and not has_tests:
            errors.append("implemented claim has no evidence target (files or tests required)")

    return errors


def check_evidence(claim: dict) -> dict:
    missing_files = [f for f in claim.get("files", []) if not Path(f).exists()]
    missing_tests = [t for t in claim.get("tests", []) if not Path(t).exists()]

    commit_status = None
    commit = claim.get("commit")
    if commit:
        result = subprocess.run(
            ["git", "cat-file", "-t", commit],
            capture_output=True,
            text=True,
        )
        commit_status = result.stdout.strip() if result.returncode == 0 else "not_found"

    return {"missing_files": missing_files, "missing_tests": missing_tests, "commit_status": commit_status}


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
    implemented_count = 0
    planned_count = 0
    claims: list[dict] = []
    failures: list[dict] = []

    for md_file in find_markdown_files(args.docs_root):
        text = md_file.read_text(encoding="utf-8")
        for block_text in CLAIM_BLOCK_RE.findall(text):
            claims_found += 1

            try:
                claim = parse_claim_block(block_text)
            except ClaimParseError as exc:
                failures.append(
                    {"id": None, "doc": str(md_file), "error": str(exc), "missing_files": [], "missing_tests": []}
                )
                continue

            errors = validate_claim(claim)
            if errors:
                failures.append(
                    {
                        "id": claim.get("id"),
                        "doc": str(md_file),
                        "error": "; ".join(errors),
                        "missing_files": [],
                        "missing_tests": [],
                    }
                )
                continue

            if claim["status"] == "planned":
                planned_count += 1
                claims.append(
                    {"id": claim["id"], "doc": str(md_file), "status": "planned", "commit_status": None}
                )
                continue

            implemented_count += 1
            evidence = check_evidence(claim)
            claims.append(
                {
                    "id": claim["id"],
                    "doc": str(md_file),
                    "status": "implemented",
                    "commit_status": evidence["commit_status"],
                }
            )
            if evidence["missing_files"] or evidence["missing_tests"]:
                failures.append(
                    {
                        "id": claim["id"],
                        "doc": str(md_file),
                        "error": None,
                        "missing_files": evidence["missing_files"],
                        "missing_tests": evidence["missing_tests"],
                    }
                )

    report = {
        "claims_found": claims_found,
        "implemented": implemented_count,
        "planned": planned_count,
        "claims": claims,
        "failures": failures,
    }
    _write_report(args.report_path, report)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
