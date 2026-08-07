"""Black-box CLI tests for scripts/verify_claims.py.

Deliberately does not import scripts/verify_claims.py as a module (no
sys.path hack) -- see docs/audits/smi-017-forensic-review/production_risks.md
#8 for why that pattern is fragile in this repo. Every test invokes the
script as a subprocess, exactly as CI does.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "verify_claims.py"


def run_verifier(docs_root: Path, report_path: Path) -> tuple[int, dict]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(docs_root), "--report-path", str(report_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return result.returncode, report


def write_doc(docs_root: Path, name: str, content: str) -> Path:
    path = docs_root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_no_claim_blocks_passes(tmp_path):
    docs_root = tmp_path / "docs"
    write_doc(docs_root, "plain.md", "# Just a normal doc\n\nNo claims here.\n")
    report_path = tmp_path / "report.json"

    code, report = run_verifier(docs_root, report_path)

    assert code == 0
    assert report["claims_found"] == 0
    assert report["failures"] == []


def test_blank_lines_inside_block_still_parse(tmp_path):
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "claim.md",
        """# A doc

```smi-018-claim
id: spacing-test
status: planned

files:
  - some/file.py

```
""",
    )
    report_path = tmp_path / "report.json"

    code, report = run_verifier(docs_root, report_path)

    assert code == 0
    assert report["claims_found"] == 1


def test_malformed_claim_missing_id_fails(tmp_path):
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "claim.md",
        """```smi-018-claim
status: implemented
files:
  - some/file.py
```
""",
    )
    report_path = tmp_path / "report.json"

    code, report = run_verifier(docs_root, report_path)

    assert code == 1
    assert len(report["failures"]) == 1
    assert report["failures"][0]["id"] is None
    assert "id" in report["failures"][0]["error"]


def test_implemented_claim_with_only_commit_fails(tmp_path):
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "claim.md",
        """```smi-018-claim
id: commit-only
status: implemented
commit: deadbeef
```
""",
    )
    report_path = tmp_path / "report.json"

    code, report = run_verifier(docs_root, report_path)

    assert code == 1
    assert len(report["failures"]) == 1
    assert report["failures"][0]["id"] == "commit-only"
    assert "evidence" in report["failures"][0]["error"]


def test_invalid_status_value_fails(tmp_path):
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "claim.md",
        """```smi-018-claim
id: bad-status
status: finished
```
""",
    )
    report_path = tmp_path / "report.json"

    code, report = run_verifier(docs_root, report_path)

    assert code == 1
    assert len(report["failures"]) == 1
    assert "status" in report["failures"][0]["error"]


def test_implemented_claim_with_real_evidence_passes(tmp_path):
    docs_root = tmp_path / "docs"
    real_file = tmp_path / "src" / "thing.py"
    real_file.parent.mkdir(parents=True)
    real_file.write_text("# real\n")
    real_test = tmp_path / "tests" / "test_thing.py"
    real_test.parent.mkdir(parents=True)
    real_test.write_text("# real test\n")

    write_doc(
        docs_root,
        "claim.md",
        f"""
```smi-018-claim
id: real-thing
status: implemented
files:
  - {real_file.relative_to(tmp_path)}
tests:
  - {real_test.relative_to(tmp_path)}
commit: not-a-real-hash-but-non-gating
```
""",
    )
    report_path = tmp_path / "report.json"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(docs_root), "--report-path", str(report_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert report["failures"] == []
    assert report["implemented"] == 1
    # commit_status was computed and actually written to the report, not
    # just checked-and-discarded -- and it's non-gating: an unresolvable
    # hash still exits 0 as long as files/tests are fine.
    assert report["claims"][0]["commit_status"] == "not_found"


def test_implemented_claim_with_missing_file_and_missing_test_fails(tmp_path):
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "claim.md",
        """
```smi-018-claim
id: missing-thing
status: implemented
files:
  - src/does_not_exist.py
tests:
  - tests/does_not_exist_either.py
```
""",
    )
    report_path = tmp_path / "report.json"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(docs_root), "--report-path", str(report_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert len(report["failures"]) == 1
    assert report["failures"][0]["missing_files"] == ["src/does_not_exist.py"]
    assert report["failures"][0]["missing_tests"] == ["tests/does_not_exist_either.py"]


def test_planned_claim_with_missing_file_still_passes(tmp_path):
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "claim.md",
        """
```smi-018-claim
id: future-thing
status: planned
files:
  - src/not_built_yet.py
```
""",
    )
    report_path = tmp_path / "report.json"

    code, report = run_verifier(docs_root, report_path)

    assert code == 0
    assert report["failures"] == []
    assert report["planned"] == 1


def test_git_unavailable_does_not_crash_and_writes_report(tmp_path):
    """Test that OSError from missing git doesn't crash the script.

    The script should still exit 0 (no gating on commit errors) and write
    the report with commit_status: "error" (distinct from "not_found").
    """
    docs_root = tmp_path / "docs"
    real_file = tmp_path / "src" / "thing.py"
    real_file.parent.mkdir(parents=True)
    real_file.write_text("# real\n")

    write_doc(
        docs_root,
        "claim.md",
        f"""
```smi-018-claim
id: thing-with-commit
status: implemented
files:
  - {real_file.relative_to(tmp_path)}
commit: deadbeef
```
""",
    )
    report_path = tmp_path / "report.json"

    # Run with PATH="", so git binary cannot be found, causing OSError.
    # This simulates a machine where git is not installed or not in PATH.
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(docs_root), "--report-path", str(report_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": ""},
    )

    # The script should still exit 0 (no gating on git errors).
    assert result.returncode == 0

    # The report should still be written (not crashed before _write_report).
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))

    # No failures (file exists, git error is non-gating).
    assert report["failures"] == []
    assert report["implemented"] == 1

    # commit_status should be "error" (distinct from "not_found").
    assert report["claims"][0]["commit_status"] == "error"


def test_excluded_paths_are_not_scanned(tmp_path):
    docs_root = tmp_path / "docs"
    bad_claim = """
```smi-018-claim
id: should-be-ignored
status: implemented
```
"""
    write_doc(docs_root, "README.md", bad_claim)
    write_doc(docs_root, "CHANGELOG.md", bad_claim)
    write_doc(docs_root, "notes/idea.md", bad_claim)
    write_doc(docs_root, "research/deep/nested.md", bad_claim)
    report_path = tmp_path / "report.json"

    code, report = run_verifier(docs_root, report_path)

    assert code == 0
    assert report["claims_found"] == 0
