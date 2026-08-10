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


def run_verifier_raw(
    docs_root: Path, report_path: Path, cwd: Path | None = None
) -> subprocess.CompletedProcess:
    """Invoke the CLI and hand back the whole result, stderr included.

    Use this when the test needs stderr or expects no report to be written.
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(docs_root), "--report-path", str(report_path)],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
    )


def run_verifier(docs_root: Path, report_path: Path) -> tuple[int, dict]:
    result = run_verifier_raw(docs_root, report_path)
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
    write_doc(docs_root, "plans/2026-01-01-some-feature.md", bad_claim)
    report_path = tmp_path / "report.json"

    code, report = run_verifier(docs_root, report_path)

    assert code == 0
    assert report["claims_found"] == 0


def test_zwsp_escaped_fence_is_not_a_claim_block(tmp_path):
    """The design spec hides its illustrative example behind a zero-width space.

    The fence regex is line-anchored precisely so that escape works: a ZWSP
    before the backticks means the line is no longer a bare fence line.
    """
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "illustrative.md",
        "# Doc\n\n```\n"
        "​```smi-018-claim\nid: illustrative-only\nstatus: implemented\n​```\n"
        "```\n",
    )
    report_path = tmp_path / "report.json"

    code, report = run_verifier(docs_root, report_path)

    assert code == 0
    assert report["claims_found"] == 0


def test_real_fence_still_matches_alongside_escaped_one(tmp_path):
    """Guard the other direction: anchoring must not break real claim blocks."""
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "mixed.md",
        "```\n"
        "​```smi-018-claim\nid: illustrative\nstatus: implemented\n​```\n"
        "```\n"
        "\n```smi-018-claim\nid: for-real\nstatus: planned\n```\n",
    )
    report_path = tmp_path / "report.json"

    code, report = run_verifier(docs_root, report_path)

    assert code == 0
    assert report["claims_found"] == 1
    assert report["claims"][0]["id"] == "for-real"


def test_failures_are_printed_to_stderr(tmp_path):
    """A CI failure has to be actionable from the log, not just the artifact."""
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "claim.md",
        """
```smi-018-claim
id: loud-failure
status: implemented
files:
  - src/definitely_absent.py
tests:
  - tests/test_definitely_absent.py
```
""",
    )
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path)

    assert result.returncode == 1
    assert "loud-failure" in result.stderr
    assert "src/definitely_absent.py" in result.stderr
    assert "tests/test_definitely_absent.py" in result.stderr
    assert str(docs_root / "claim.md") in result.stderr


def test_non_utf8_markdown_is_a_failure_not_a_crash(tmp_path):
    """An undecodable doc is an unknown evidence state, so it must fail."""
    docs_root = tmp_path / "docs"
    docs_root.mkdir(parents=True)
    bad = docs_root / "mojibake.md"
    # 0xe9 is a bare latin-1 'e-acute' -- invalid as UTF-8.
    bad.write_bytes(b"# Doc\n\nCaf\xe9 not valid utf-8\n")
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path)

    assert result.returncode == 1
    # The report must still have been written -- no crash before _write_report.
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert len(report["failures"]) == 1
    assert report["failures"][0]["doc"] == str(bad)
    assert "could not read file" in report["failures"][0]["error"]
    assert "mojibake.md" in result.stderr


def test_missing_docs_root_fails_closed(tmp_path):
    """A typo'd docs_root must not silently pass as 'no claims found'."""
    docs_root = tmp_path / "docs-typo"
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path)

    # Exit 2 == invocation error, distinct from 1 == real claim failures.
    assert result.returncode == 2
    assert "docs-typo" in result.stderr
    # No report: there is nothing to report about, and an empty report would
    # look like a clean run to anything reading the artifact.
    assert not report_path.exists()


def test_docs_root_that_is_a_file_fails_closed(tmp_path):
    docs_root = tmp_path / "not-a-dir.md"
    docs_root.write_text("# nope\n", encoding="utf-8")
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path)

    assert result.returncode == 2
    assert result.stderr.strip()


def test_absolute_path_evidence_fails(tmp_path):
    """Path.exists() is true for /etc/hosts -- that must not satisfy a claim."""
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "claim.md",
        """
```smi-018-claim
id: absolute-path
status: implemented
files:
  - /etc/hosts
```
""",
    )
    report_path = tmp_path / "report.json"

    code, report = run_verifier(docs_root, report_path)

    assert code == 1
    assert report["failures"][0]["missing_files"] == ["/etc/hosts"]


def test_directory_evidence_fails(tmp_path):
    """A directory exists but is not evidence of any implementation."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    docs_root = repo / "docs"
    write_doc(
        docs_root,
        "claim.md",
        """
```smi-018-claim
id: directory-not-file
status: implemented
files:
  - .
  - src
```
""",
    )
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path, cwd=repo)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert report["failures"][0]["missing_files"] == [".", "src"]


def test_path_escaping_repo_root_fails(tmp_path):
    """The referenced file genuinely exists -- but outside the repo root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("# real file, wrong side of the fence\n", encoding="utf-8")
    docs_root = repo / "docs"
    write_doc(
        docs_root,
        "claim.md",
        """
```smi-018-claim
id: escaping-path
status: implemented
files:
  - ../outside.py
```
""",
    )
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path, cwd=repo)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert outside.is_file()
    assert result.returncode == 1
    assert report["failures"][0]["missing_files"] == ["../outside.py"]


def test_duplicate_key_in_block_fails(tmp_path):
    """Last-value-wins would let a doc read 'implemented' while checking 'planned'."""
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "claim.md",
        """
```smi-018-claim
id: two-statuses
status: implemented
files:
  - src/does_not_exist.py
status: planned
```
""",
    )
    report_path = tmp_path / "report.json"

    code, report = run_verifier(docs_root, report_path)

    assert code == 1
    assert len(report["failures"]) == 1
    assert "duplicate key" in report["failures"][0]["error"]
    assert "status" in report["failures"][0]["error"]
    # Not silently resolved to the trailing `planned` (which would have passed).
    assert report["planned"] == 0


def test_duplicate_list_key_in_block_fails(tmp_path):
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "claim.md",
        """
```smi-018-claim
id: two-file-lists
status: implemented
files:
  - scripts/verify_claims.py
files:
  - src/does_not_exist.py
```
""",
    )
    report_path = tmp_path / "report.json"

    code, report = run_verifier(docs_root, report_path)

    assert code == 1
    assert "duplicate key" in report["failures"][0]["error"]


# ---------------------------------------------------------------------------
# PROSE FABRICATION (v0.2) -- closes the dd66dd3 gap
# ---------------------------------------------------------------------------


def test_prose_fabrication_with_missing_file_fails(tmp_path):
    """The dd66dd3 shape: 'Tests: N/N passing' + a file that does not exist.

    No smi-018-claim block is present -- this is the exact gap the v0.2
    detector closes.
    """
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "status.md",
        """# Build status

Tests: 53/53 passing. The factory lives in `core/factory.py`.
""",
    )
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path, cwd=tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert len(report["failures"]) == 1
    assert "prose fabrication" in report["failures"][0]["error"]
    assert "core/factory.py (line 3)" in report["failures"][0]["missing_files"]
    # The failure is prose-only -- no claim block was involved.
    assert report["claims_found"] == 0


def test_prose_no_signature_no_failure(tmp_path):
    """Mentioning a not-yet-built path is fine -- plans are not fabrications.

    The hard-signature gate is what separates a roadmap from a false
    completed-checkpoint report.
    """
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "plan.md",
        """# Roadmap

Next quarter we intend to build `core/factory.py` and its adapters.
""",
    )
    report_path = tmp_path / "report.json"

    code, report = run_verifier(docs_root, report_path)

    assert code == 0
    assert report["failures"] == []


def test_prose_negated_missing_path_passes(tmp_path):
    """Forensic records must be able to say a file DOES NOT exist."""
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "incident.md",
        """# Incident record

Tests: 53/53 passing was claimed, but `core/factory.py` does not exist
anywhere in this repository.
""",
    )
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path, cwd=tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert report["failures"] == []


def test_prose_no_quote_negation_passes(tmp_path):
    """RECONCILIATION.md's exact phrasing: 'No X, Y, or Z exists anywhere'.

    The 'no ... exists' pattern is the long-form negation the forensic
    review uses -- covered by the widened regex.
    """
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "incident.md",
        """# Incident record

Tests: 53/53 passing. No core/factory.py, core/contracts/, or
core/orchestrator/router.py exists anywhere in this repository.
""",
    )
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path, cwd=tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert report["failures"] == []


def test_prose_exempt_marker_passes(tmp_path):
    """A curated incident record can opt out with a visible source marker.

    RECONCILIATION.md is the one real doc that must quote a fabrication to
    refute it; the marker is an HTML comment in the source, not a hidden
    flag, and requires an explicit justification.
    """
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "incident.md",
        """<!-- verify-claims: prose-exempt: quotes the dd66dd3 fabrication to refute it -->

# Incident record

Tests: 53/53 passing was the fabricated claim; `core/factory.py` never
existed on any branch.
""",
    )
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path, cwd=tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert report["failures"] == []
    assert report["prose_exempt"] == 1


def test_prose_exempt_marker_without_reason_is_not_honored(tmp_path):
    """The marker must carry an explicit justification.

    A bare `<!-- verify-claims: prose-exempt -->` (no reason) does not
    match the strict marker regex, so the doc is scanned like any other --
    the reason requirement keeps the escape hatch honest and auditable.
    Here the doc asserts existence positively (no negation to save it), so
    the un-honored marker must leave the fabrication to FAIL.
    """
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "incident.md",
        """<!-- verify-claims: prose-exempt -->

# Incident record

Tests: 53/53 passing. The factory is live in `core/factory.py`.
""",
    )
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path, cwd=tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["prose_exempt"] == 0
    assert result.returncode == 1
    assert "prose fabrication" in report["failures"][0]["error"]


def test_prose_plain_checklist_roadmap_passes(tmp_path):
    """A roadmap with plain task-list checkboxes is NOT a fabrication.

    Review fix: a bare "[x]" checkbox fires the old signature, so any
    checklist roadmap mentioning a not-yet-built path false-positived.
    Plain "- [x] research" is not a completed-state claim -- only
    verification-context checkboxes ("definition-of-done items checked
    [x]") are.
    """
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "roadmap.md",
        """# Roadmap

- [x] Research phase
- [x] Spike complete
- [ ] Build `core/factory.py`
- [ ] Wire `adapters/ghl/ghl_client.py`
""",
    )
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path, cwd=tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert report["failures"] == []


def test_prose_verification_context_checkbox_fails(tmp_path):
    """The dd66dd3 shape -- 'definition-of-done items checked [x]' -- trips.

    Review fix: the [x] alternative is narrowed to verification context,
    but the original incident phrasing must still be caught.
    """
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "status.md",
        """# Phase 2

Definition-of-done items checked [x]: `core/factory.py`, `core/registry/`,
`adapters/prime_agent/` -- all built and live.
""",
    )
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path, cwd=tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert len(report["failures"]) == 1
    assert "prose fabrication" in report["failures"][0]["error"]


def test_prose_traversal_reference_skipped(tmp_path):
    """A traversal reference escapes the repo root; it is not a claim.

    Review fix: the old lstrip-then-check turned "../core/factory.py" into
    a repo-relative "core/factory.py" and the startswith("..") guard was
    dead code. Traversal is now rejected before normalization.
    """
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "status.md",
        """# Build status

Tests: 53/53 passing. See `../core/factory.py` for the implementation.
""",
    )
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path, cwd=tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert report["failures"] == []


def test_prose_real_path_with_signature_passes(tmp_path):
    """A genuine completed doc referencing a file that actually exists is fine."""
    real_file = tmp_path / "core" / "factory.py"
    real_file.parent.mkdir(parents=True)
    real_file.write_text("# real\n", encoding="utf-8")
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "status.md",
        """# Build status

Tests: 12/12 passing. The factory lives in `core/factory.py`.
""",
    )
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path, cwd=tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert report["failures"] == []


def test_prose_glob_uri_template_skipped(tmp_path):
    """Globs, URIs, and brace/angle templates are never file claims."""
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "status.md",
        """# Build status

Tests: 12/12 passing.

- `artifacts/h10_*.json`
- `ledger://claims/abc123`
- `https://example.com/readme.py`
- `/etc/hosts`
- `~/code/thing.py`
- `core/{env}/factory.py`
- `core/<id>/factory.py`
""",
    )
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path, cwd=tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert report["failures"] == []


def test_prose_bare_filename_not_a_claim(tmp_path):
    """A bare filename with no directory component carries no provenance."""
    docs_root = tmp_path / "docs"
    write_doc(
        docs_root,
        "status.md",
        """# Build status

Tests: 12/12 passing. See factory.py for details.
""",
    )
    report_path = tmp_path / "report.json"

    result = run_verifier_raw(docs_root, report_path, cwd=tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert report["failures"] == []
