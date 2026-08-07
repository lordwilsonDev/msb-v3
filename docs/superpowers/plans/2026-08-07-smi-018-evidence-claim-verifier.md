# SMI-018 v0.1 Evidence Claim Verifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone CI script that scans markdown docs for structured `smi-018-claim` blocks and fails the build when a claim of `status: implemented` references a file or test that doesn't actually exist in the repository.

**Architecture:** A single dependency-free Python script (`scripts/verify_claims.py`) does everything — extract fenced claim blocks from `docs/**/*.md`, parse them with a hand-rolled key/value+list grammar, validate required fields, check `files`/`tests` paths with `Path.exists()`, report (never gate on) `commit` via `git cat-file -t`, and write a machine-readable JSON report. A new `claims` job in `.github/workflows/ci.yml` runs it on every push/PR, wired into the existing `notify` and `deploy` jobs' pass/fail logic the same way `test`/`lint`/`security`/`docker` already are.

**Tech Stack:** Python 3.11+ stdlib only (`re`, `json`, `argparse`, `subprocess`, `pathlib`, `sys`) — no new dependency. GitHub Actions (existing `ci.yml` conventions: `actions/checkout@v4`, `actions/setup-python@v5`, `actions/upload-artifact@v4`).

## Global Constraints

- Zero new dependencies (no PyYAML) — hand-rolled parser only.
- Zero changes under `src/msb_v3/` — this is not a runtime subsystem.
- No persistent ledger/database — CI pass/fail + the JSON report is the only record.
- Script lives at `scripts/verify_claims.py`; its tests at `tests/test_verify_claims.py`.
- Tests are black-box only: invoke the script via `subprocess.run([sys.executable, "scripts/verify_claims.py", ...])`. Never `import` `scripts/verify_claims.py` as a module or use a `sys.path.insert` hack to make it importable — that exact pattern is a cited fragility root-cause in `docs/audits/smi-017-forensic-review/production_risks.md` (#8).
- Report path defaults to `artifacts/smi018/claim_report.json`, overridable via `--report-path` (so tests point it at `tmp_path`).
- All `files`/`tests` paths in claim blocks, and the `docs_root`/`--report-path` CLI arguments, resolve relative to the process's current working directory (`Path.cwd()`) — the script never takes or needs an explicit repo-root argument.
- Full design rationale: `docs/superpowers/specs/2026-08-07-smi-018-evidence-claim-verifier-design.md`.

---

### Task 1: Script scaffolding — block extraction and claim parsing

**Files:**
- Create: `scripts/verify_claims.py`
- Create: `tests/test_verify_claims.py`

**Interfaces:**
- Produces: `CLAIM_BLOCK_RE` (compiled regex), `ClaimParseError` (exception class), `parse_claim_block(text: str) -> dict[str, str | list[str]]`, `find_markdown_files(docs_root: Path) -> list[Path]`, `main(argv: list[str] | None = None) -> int`. Later tasks import none of these directly (black-box testing only) but rely on `main`'s CLI contract: `python scripts/verify_claims.py <docs_root> [--report-path PATH]`, and on the report JSON having top-level keys `claims_found`, `implemented`, `planned`, `claims`, `failures`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_verify_claims.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_verify_claims.py -v`
Expected: both tests FAIL — `scripts/verify_claims.py` does not exist yet (`FileNotFoundError` or non-zero exit from `subprocess.run` failing to find the script, or `json.loads` failing because `report_path` was never written).

- [ ] **Step 3: Write the minimal implementation**

Create `scripts/verify_claims.py`:

```python
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
```

Note: `"claims": []` is included from the start even though nothing populates
it yet, so the top-level report shape is stable from Task 1 onward — Task 2
starts appending planned/implemented entries to it, Task 3 fills in each
entry's `commit_status`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_verify_claims.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_claims.py tests/test_verify_claims.py
git commit -m "feat(smi-018): scaffold claim block extraction and parsing"
```

---

### Task 2: Validation — required fields, status enum, evidence-target rule

**Files:**
- Modify: `scripts/verify_claims.py`
- Modify: `tests/test_verify_claims.py`

**Interfaces:**
- Consumes: `parse_claim_block`, `ClaimParseError`, `find_markdown_files`, `CLAIM_BLOCK_RE`, `_write_report` from Task 1 (unchanged signatures).
- Produces: `validate_claim(claim: dict) -> list[str]` (returns a list of human-readable error strings, empty if valid). `main`'s report gains real `implemented`/`planned` counts, a `claims` list with one entry per successfully-validated claim (`{"id": str, "doc": str, "status": "planned"|"implemented", "commit_status": str | None}` — `commit_status` stays `None` until Task 3 wires in the actual check), and populates `failures` for malformed/invalid claims. Failure records have the shape `{"id": str | None, "doc": str, "error": str | None, "missing_files": list[str], "missing_tests": list[str]}` — Task 3 will populate `missing_files`/`missing_tests`; this task always leaves them as `[]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_claims.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_verify_claims.py -v`
Expected: the three new tests FAIL — `main` currently never validates anything or returns 1, so `code == 0` and `report["failures"] == []` for all three, failing the assertions.

- [ ] **Step 3: Write the minimal implementation**

In `scripts/verify_claims.py`, add `validate_claim` (place it directly below `parse_claim_block`):

```python
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
```

Replace the body of `main` with:

```python
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
            # Evidence checking (files/tests) and the real commit_status
            # lookup are added in Task 3; for now record the claim with
            # commit_status left as None.
            claims.append(
                {"id": claim["id"], "doc": str(md_file), "status": "implemented", "commit_status": None}
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_verify_claims.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_claims.py tests/test_verify_claims.py
git commit -m "feat(smi-018): validate required fields and evidence-target rule"
```

---

### Task 3: Evidence checking — files, tests, and informational commit lookup

**Files:**
- Modify: `scripts/verify_claims.py`
- Modify: `tests/test_verify_claims.py`

**Interfaces:**
- Consumes: everything from Tasks 1-2 unchanged.
- Produces: `check_evidence(claim: dict) -> dict` returning `{"missing_files": list[str], "missing_tests": list[str], "commit_status": str | None}`. `main` now populates `missing_files`/`missing_tests` in failure records for `implemented` claims, and fills in the real `commit_status` (instead of Task 2's placeholder `None`) on every `implemented` claim's entry in the `claims` list — this is what actually satisfies "parses and reports (never gates on) referenced commits," since a value computed but never written to the report wouldn't be "reported" in any meaningful sense.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify_claims.py`:

```python
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
        f"""```smi-018-claim
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
        """```smi-018-claim
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
        """```smi-018-claim
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
```

Note: `test_implemented_claim_with_real_evidence_passes` and
`test_implemented_claim_with_missing_file_and_missing_test_fails` run the subprocess with
`cwd=tmp_path` (not `cwd=REPO_ROOT` like `run_verifier`'s default) because
`files`/`tests` paths resolve relative to the process's working directory —
these two tests need that cwd to be `tmp_path` so the relative paths they
write resolve correctly. `run_verifier` keeps `cwd=REPO_ROOT` for the other
tests since they don't reference real files.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_verify_claims.py -v`
Expected: the three new tests FAIL — nothing checks `files`/`tests` against the filesystem yet, so `implemented` claims always report zero failures regardless of whether their paths exist.

- [ ] **Step 3: Write the minimal implementation**

In `scripts/verify_claims.py`, add near the top (after the `import` block):

```python
import subprocess
```

Add `check_evidence` directly below `validate_claim`:

```python
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
```

In `main`, replace:

```python
            implemented_count += 1
            # Evidence checking (files/tests) and the real commit_status
            # lookup are added in Task 3; for now record the claim with
            # commit_status left as None.
            claims.append(
                {"id": claim["id"], "doc": str(md_file), "status": "implemented", "commit_status": None}
            )
```

with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_verify_claims.py -v`
Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_claims.py tests/test_verify_claims.py
git commit -m "feat(smi-018): check files/tests existence, report commit informationally"
```

---

### Task 4: Path exclusions, CI job, and dog-food claim

**Files:**
- Modify: `scripts/verify_claims.py`
- Modify: `tests/test_verify_claims.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/superpowers/specs/2026-08-07-smi-018-evidence-claim-verifier-design.md`

**Interfaces:**
- Consumes: everything from Tasks 1-3 unchanged.
- Produces: `find_markdown_files` now excludes `docs/README.md`, `docs/CHANGELOG.md`, and anything under a `notes/` or `research/` directory. No new functions — this is the last code change to `scripts/verify_claims.py` for v0.1.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_verify_claims.py`:

```python
def test_excluded_paths_are_not_scanned(tmp_path):
    docs_root = tmp_path / "docs"
    bad_claim = """```smi-018-claim
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_verify_claims.py -v`
Expected: FAILS — `find_markdown_files` currently scans every `*.md` file with no exclusions, so all four excluded claim blocks (each missing `files`/`tests`, since they're `implemented` with nothing else) get parsed and fail validation, making `report["claims_found"] == 4` and `code == 1`.

- [ ] **Step 3: Write the minimal implementation**

In `scripts/verify_claims.py`, replace `find_markdown_files`:

```python
_EXCLUDED_FILENAMES = {"README.md", "CHANGELOG.md"}
_EXCLUDED_DIR_NAMES = {"notes", "research"}


def find_markdown_files(docs_root: Path) -> list[Path]:
    result = []
    for path in sorted(docs_root.rglob("*.md")):
        if path.name in _EXCLUDED_FILENAMES and path.parent == docs_root:
            continue
        rel_parts = path.relative_to(docs_root).parts[:-1]
        if any(part in _EXCLUDED_DIR_NAMES for part in rel_parts):
            continue
        result.append(path)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_verify_claims.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Add the `claims` CI job**

In `.github/workflows/ci.yml`, insert the following job immediately after the `docker:` job (i.e., between the end of the `docker` job's last step and the `notify:` job header — after the line `          docker rm -f msb-v3-test` and before the blank line that precedes `  notify:`):

```yaml
  claims:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Verify evidence claims
        run: python scripts/verify_claims.py docs/

      - name: Upload claim report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: claim-report
          path: artifacts/smi018/claim_report.json
          retention-days: 7
```

Note this job has no `needs: preflight` and no `if:` gate on `needs.preflight.outputs.changed` — unlike the other four jobs. It scans the full `docs/` tree on every run by design (see "Scan scope" in the spec: a claim can be falsified by an unrelated later commit deleting its claimed file, so diff-scoping or skip-if-unchanged would miss that). It always runs on every push and PR to `main`.

Then update the `notify` job. Replace:

```yaml
  notify:
    needs: [test, lint, security, docker]
    if: always() && (needs.test.result == 'failure' || needs.lint.result == 'failure' || needs.security.result == 'failure' || needs.docker.result == 'failure')
```

with:

```yaml
  notify:
    needs: [test, lint, security, docker, claims]
    if: always() && (needs.test.result == 'failure' || needs.lint.result == 'failure' || needs.security.result == 'failure' || needs.docker.result == 'failure' || needs.claims.result == 'failure')
```

Then update the `deploy` job's `needs` line. Replace:

```yaml
  deploy:
    needs: [test, lint, security, docker]
```

with:

```yaml
  deploy:
    needs: [test, lint, security, docker, claims]
```

- [ ] **Step 6: Add the dog-food claim block**

Append to the end of `docs/superpowers/specs/2026-08-07-smi-018-evidence-claim-verifier-design.md`:

```
## Self-verification

​```smi-018-claim
id: smi018-evidence-verifier
status: implemented
files:
  - scripts/verify_claims.py
tests:
  - tests/test_verify_claims.py
​```
```

- [ ] **Step 7: Run the full test suite and the verifier against the real repo**

Run: `pytest tests/ -v`
Expected: all tests pass, including the 9 in `tests/test_verify_claims.py`.

Run: `python scripts/verify_claims.py docs/`
Expected: exit code 0. This is the dog-food check — it must find the `smi018-evidence-verifier` claim block just added, see `status: implemented`, confirm `scripts/verify_claims.py` and `tests/test_verify_claims.py` both exist, and pass. Inspect `artifacts/smi018/claim_report.json` to confirm `"claims_found": 1, "implemented": 1, "failures": []`.

- [ ] **Step 8: Commit**

```bash
git add scripts/verify_claims.py tests/test_verify_claims.py .github/workflows/ci.yml docs/superpowers/specs/2026-08-07-smi-018-evidence-claim-verifier-design.md
git commit -m "feat(smi-018): add path exclusions, wire claims job into CI, dog-food the verifier"
```

---

## Post-plan verification

After all 4 tasks are committed:
- [ ] `pytest tests/ -v` passes in full (not just `test_verify_claims.py` — confirms nothing else broke).
- [ ] `python scripts/verify_claims.py docs/` exits 0 against the real repo state (confirms the dog-food claim and the existing `docs/audits/smi-017-forensic-review/*.md` files, which have no claim blocks, don't trip anything).
- [ ] Every acceptance criterion in `docs/superpowers/specs/2026-08-07-smi-018-evidence-claim-verifier-design.md`'s "Acceptance criteria (v0.1 = done when)" section is satisfied — cross-check the list directly against the 9 tests and the dog-food step above.
