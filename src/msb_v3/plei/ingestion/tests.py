"""Test suite ingestion — structure, counts, coverage stats.

Walks ``tests/`` and counts files, tests, and runs pytest --collect-only
to get the live test count (hermetic, no execution).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from msb_v3.plei.provenance import Provenance, Provenanced


@dataclass(slots=True)
class TestFacts:
    """Test suite facts."""

    file_count: Provenanced = field(default_factory=Provenanced.unknown)
    collected_tests: Provenanced = field(default_factory=Provenanced.unknown)
    test_dirs: Provenanced = field(default_factory=Provenanced.unknown)  # top-level dirs in tests/


def ingest_tests(project_root: str | Path) -> TestFacts:
    """Ingest test-suite structure."""
    root = Path(project_root).resolve()
    facts = TestFacts()
    source_tag = f"ingestion/tests ({root.name})"

    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        facts.file_count = Provenanced(value=0, provenance=Provenance.UNKNOWN, source="tests/ not found")
        return facts

    test_files = list(tests_dir.rglob("test_*.py"))
    facts.file_count = Provenanced.observed(len(test_files), source_tag)

    # Test subdirectories
    subdirs = sorted(d.name for d in tests_dir.iterdir() if d.is_dir())
    facts.test_dirs = Provenanced.observed(subdirs, source_tag)

    # Try pytest --collect-only for live count
    try:
        proc = subprocess.run(
            ["python", "-m", "pytest", "--collect-only", "-q", "--no-header"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Parse last line: "XXX tests collected in Y.YYs"
        last_lines = [line.strip() for line in proc.stdout.split("\n") if line.strip()]
        for line in reversed(last_lines):
            if "test" in line and ("collected" in line or "passed" in line or "selected" in line):
                parts = line.split()
                for p in parts:
                    if p.replace(",", "").isdigit():
                        facts.collected_tests = Provenanced.observed(
                            int(p.replace(",", "")), "pytest --collect-only"
                        )
                        break
                break
        if facts.collected_tests.value is None:
            facts.collected_tests = Provenanced.inferred(
                len(last_lines), f"pytest --collect-only (line count from: {last_lines[-1][:80] if last_lines else 'empty'})"
            )
    except Exception:
        facts.collected_tests = Provenanced.inferred(
            len(test_files), "file count (pytest unavailable)"
        )

    return facts


def test_facts_as_dict(facts: TestFacts) -> dict[str, Any]:
    return {k: getattr(facts, k).as_dict() for k in (
        "file_count", "collected_tests", "test_dirs")}