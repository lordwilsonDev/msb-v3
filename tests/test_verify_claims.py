"""Black-box CLI tests for scripts/verify_claims.py.

Deliberately does not import scripts/verify_claims.py as a module (no
sys.path hack) -- see docs/audits/smi-017-forensic-review/production_risks.md
#8 for why that pattern is fragile in this repo. Every test invokes the
script as a subprocess, exactly as CI does.
"""
from __future__ import annotations

import json
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
