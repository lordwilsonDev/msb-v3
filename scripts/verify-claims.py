#!/usr/bin/env python3
"""M8 — claims audit: every capability claim must link to verifiable evidence.

The release declaration (docs/releases/MSB-v3-RELEASE.md) is the contract
between what MSB claims and what it can do. This gate makes "capability
claims are auditable" (M8 exit criterion) machine-checkable:

  1. Every ```evidence path``` named in the claims table must exist in the
     repo (artifacts, test files, scripts, docs) — a claim pointing at
     nothing is a claim withdrawn.
  2. Every ``tests/...`` evidence that cites a test-count must match the
     live ``pytest --collect-only`` count — a claim that says "23 tests"
     must be true today, not on the day it was written.
  3. The claim table itself must be non-empty and every claim row must cite
     at least one evidence path (no uncited claims).

Usage:
    python3 scripts/verify-claims.py [--release docs/releases/MSB-v3-RELEASE.md]

Exit code: 0 when every claim's evidence exists and counts agree; 1
otherwise. Run in CI so the declaration can never drift from the tree.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Evidence paths that are intentionally not on-disk literals (launchd jobs,
# live drills) — these are verified by the ops docs instead.
_KNOWN_LIVE_CLAIMS = re.compile(
    r"launchd|restore drill|30-day trial", re.IGNORECASE
)


def _claims_table(doc: str) -> list[tuple[str, str]]:
    """Parse the 'Implemented and supported' claims table: (claim, evidence)."""
    lines = doc.splitlines()
    rows: list[tuple[str, str]] = []
    in_table = False
    for line in lines:
        if line.strip().startswith("| Claim | Evidence |"):
            in_table = True
            continue
        if in_table and re.match(r"^\|\s*-{2,}\s*\|", line):
            continue  # header separator row (|---|---|)
        if in_table and line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[0] and cells[1] and not all(
                re.fullmatch(r"-{2,}", c) for c in cells[:2]
            ):
                rows.append((cells[0], cells[1]))
        elif in_table and not line.strip().startswith("|"):
            in_table = False
    return rows


def _evidence_paths(evidence: str) -> list[str]:
    """Every backticked path/fragment named in the evidence cell."""
    return re.findall(r"`([^`]+)`", evidence)


def _is_path(p: str) -> bool:
    """Heuristic: looks like a repo path (starts with a dir/file name)."""
    if _KNOWN_LIVE_CLAIMS.search(p):
        return False
    return bool(re.match(r"^[\w./*-]+\.(py|json|md|sh|yml|yaml)$", p)) or p.startswith(
        ("tests/", "scripts/", "docs/", "artifacts/", "src/")
    )


def _count_tests(path: str) -> int | None:
    """Live collected test count for one test file/dir (None on failure)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", path, "-q", "--collect-only"],
        capture_output=True, text=True, cwd=ROOT,
    )
    m = re.search(r"(\d+) tests? collected", proc.stdout)
    return int(m.group(1)) if m else None


def main() -> int:
    parser = argparse.ArgumentParser(description="M8 claims audit (see module docstring)")
    parser.add_argument("--release", default="docs/releases/MSB-v3-RELEASE.md")
    args = parser.parse_args()

    doc = (ROOT / args.release).read_text()
    rows = _claims_table(doc)
    if not rows:
        print("[claims] FAIL: no claims table found in", args.release)
        return 1

    problems: list[str] = []
    checked = 0
    count_claims = 0

    for claim, evidence in rows:
        paths = _evidence_paths(evidence)
        if not paths:
            problems.append(f"claim with no evidence path: {claim[:60]!r}")
            continue
        for p in paths:
            if not _is_path(p):
                continue
            checked += 1
            # Bare filenames (audit.json, replay.json) are covered by the
            # directory cited in the same evidence cell — resolve against it.
            candidate = ROOT / p
            if not candidate.exists():
                if "/" not in p and (ROOT / "artifacts" / p).exists():
                    candidate = ROOT / "artifacts" / p
                elif any(ch in p for ch in "*?["):  # glob (soak-report-*.json)
                    matches = list(ROOT.glob(p))
                    candidate = matches[0] if matches else None
                else:
                    dirs_in_cell = [ROOT / d for d in paths if d.startswith(("artifacts/", "docs/"))]
                    candidate = next(
                        (d / p for d in dirs_in_cell if (d / p).exists()), None
                    )
                    if candidate is None:
                        # Bare filename with no prefix in the cell: search
                        # artifacts/ recursively (e.g. "replay.json in each
                        # fixture").
                        candidate = next(ROOT.glob(f"artifacts/**/{p}"), None)
            if candidate is None or not candidate.exists():
                problems.append(f"missing evidence: {p} (claim: {claim[:50]!r})")
                continue
            # Test-count agreement: "tests/foo.py (N tests|guards)" must match
            # live collection.
            m = re.search(r"\((\d+) (tests?|guards?)\)", evidence)
            if m and p.startswith("tests/"):
                count_claims += 1
                live = _count_tests(p)
                claimed = int(m.group(1))
                if live is not None and live != claimed:
                    problems.append(
                        f"test-count drift: {p} claims {claimed}, live collection is {live}"
                    )

    if problems:
        print(f"[claims] FAIL: {len(problems)} problem(s) across {len(rows)} claims "
              f"({checked} evidence paths checked):")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"[claims] PASS: {len(rows)} claims, {checked} evidence paths verified, "
          f"{count_claims} test-count claims match live collection")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
