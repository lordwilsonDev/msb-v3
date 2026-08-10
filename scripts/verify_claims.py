#!/usr/bin/env python3
"""SMI-018 v0.2 -- Evidence Claim Verifier.

Two checks:

1. CLAIM BLOCKS (v0.1): scans markdown files for ```smi-018-claim fenced
   blocks and checks that any block with status: implemented references
   files/tests that actually exist in the repository.

2. PROSE FABRICATION (v0.2, closes the dd66dd3 gap): catches a document
   that asserts a COMPLETED, VERIFIED state while referencing files that do
   not exist — without any smi-018-claim block. The dd66dd3 incident
   (fabricated Phase 2 audit docs, removed 2026-08-10) was exactly this:
   "Tests: 53/53 passing" + core/factory.py that never existed.

   Design (kept conservative to avoid false-flagging the incident records
   themselves, which must be able to NEGATE non-existent artifacts):

   - HARD-SIGNATURE GATED: a doc only trips when it ALSO asserts a
     completed-verification claim ("Tests: N/N passing", "brought to
     green", "definition of done", "all tests pass", or a "[x]"
     checkbox in verification context like "definition-of-done items
     checked [x]"). A doc that merely mentions a not-yet-existing path
     (a plan, a proposal, a roadmap) never trips — no signature, no
     failure. Plain task-list checkboxes ("- [x] research phase") are
     NOT signatures: a checklist item alone is not a completed-state
     claim.
   - NEGATION-AWARE: a missing path is skipped when its nearby context
     says "does not exist" / "never existed" / "fabricated" / "absent" —
     the forensic review must be able to say "core/factory.py does not
     exist" without failing itself.
   - OPT-OUT MARKER: a curated incident-record document can declare
     `<!-- verify-claims: prose-exempt: <reason> -->` (visible in the
     source, reason required, not a hidden flag) when it must quote a
     fabrication as part of documenting it — e.g. RECONCILIATION.md,
     which quotes "Tests: 53/53 passing" while proving the underlying
     files never existed. The exemption is a human-reviewed deviation,
     surfaced in the report via the prose_exempt counter.
   - SHAPED-TOKEN FILTERING: absolute paths, URLs, globs, brace/angle
     templates, traversal (../) references, and non-source extensions
     are never treated as claims.

   Known conservative-by-design limitations (documented, not fixed):
   - Paths wrapped across line breaks evade the per-line token regex.
   - "53/53" without the word "tests", or "all tests are passing"
     (non-adjacent verb), evade the signature regex.
   - .md references are intentionally excluded (docs referencing docs is
     too common to gate); the gap dd66dd3 exploited was code files.
   The gate errs toward NOT flagging; it is one layer, not a proof.

See docs/superpowers/specs/2026-08-07-smi-018-evidence-claim-verifier-design.md
for the v0.1 design and rationale.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Line-anchored on purpose: the opening and closing fence must each be the
# ENTIRE line. That makes the zero-width-space escape used in the design
# spec's illustrative example actually work -- a ZWSP before the backticks
# means the line no longer starts with "```", so it is not a claim block.
CLAIM_BLOCK_RE = re.compile(r"^```smi-018-claim$\n(.*?)\n^```$", re.DOTALL | re.MULTILINE)


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
        if key in fields:
            # A duplicate key would silently win by dict overwrite, so a human
            # skimming top-to-bottom could read a different status than the one
            # actually checked. Unknown evidence state is a failure state.
            raise ClaimParseError(f"duplicate key in claim block: {key!r}")
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


def is_valid_evidence_path(raw: str) -> bool:
    """Return True only for a repo-relative path naming a real file.

    `Path.exists()` alone is a gate bypass: it is true for directories, for
    `.`, and for absolute paths like `/etc`, so a claim could satisfy the
    gate without pointing at any real evidence. Same defensive shape as
    `src/msb_v3/api/mcp_bridge.py`'s `_normalize_vault_path`: reject
    absolutes, resolve, confirm containment, and only then check the target.
    """
    raw = raw.strip()
    if not raw:
        return False

    candidate = Path(raw)
    if candidate.is_absolute():
        return False

    root = Path.cwd().resolve()
    try:
        resolved = (root / candidate).resolve()
        resolved.relative_to(root)
    except (ValueError, OSError):
        return False

    # .is_file(), not .exists() -- a directory is not evidence.
    return candidate.is_file()


def check_evidence(claim: dict) -> dict:
    missing_files = [f for f in claim.get("files", []) if not is_valid_evidence_path(f)]
    missing_tests = [t for t in claim.get("tests", []) if not is_valid_evidence_path(t)]

    commit_status = None
    commit = claim.get("commit")
    if commit:
        try:
            result = subprocess.run(
                # `--` so a commit value that looks like a flag is still
                # treated as an object name; timeout so a wedged git can't
                # hang CI.
                ["git", "cat-file", "-t", "--", commit],
                capture_output=True,
                text=True,
                timeout=10,
            )
            commit_status = result.stdout.strip() if result.returncode == 0 else "not_found"
        except (OSError, subprocess.SubprocessError):
            # TimeoutExpired is a SubprocessError, not an OSError.
            commit_status = "error"

    return {"missing_files": missing_files, "missing_tests": missing_tests, "commit_status": commit_status}


# ---------------------------------------------------------------------------
# PROSE FABRICATION DETECTION (v0.2)
# ---------------------------------------------------------------------------

# A doc may declare itself a curated incident record that must be able to
# QUOTE a fabrication as part of refuting it (e.g. RECONCILIATION.md quotes
# "Tests: 53/53 passing"). The marker is an HTML comment with a REQUIRED
# reason -- visible in the source, auditable, never a hidden config flag.
PROSE_EXEMPT_RE = re.compile(r"<!--\s*verify-claims:\s*prose-exempt\s*:\s*[^>\n]+")    # Completed-verification assertions. The dd66dd3 fabrication used exactly
    # these shapes ("Tests: 53/53 passing.", "definition-of-done items checked
    # [x]", "brought to green"). Only docs asserting a DONE state can trip;
    # plans/proposals/roadmaps never do. The [x] alternative is deliberately
    # NARROWED to verification language PRECEDING the checkbox ("items
    # checked [x]") -- a bare task-list checkbox ("- [x] research" or "- [x]
    # spike complete", where the checkbox leads) is not a completed-state
    # claim, and making it one would false-positive every checklist roadmap
    # that mentions a not-yet-built path.
PROSE_SIGNATURE_RE = re.compile(
    r"tests\s*:?\s*\d+\s*/\s*\d+\s+passing"
    r"|brought\s+to\s+green"
    r"|definition[- ]of[- ]done"
    r"|all\s+tests?\s+pass"
    r"|(?:definition|checked|complete|verif|pass|green|done|ship)[^\n]{0,60}\[x\]",
    re.IGNORECASE,
)

# A missing path whose nearby context negates its existence is not a claim
# of existence. The forensic review must be able to name non-existent files
# while proving they never existed.
PROSE_NEGATION_RE = re.compile(
    r"does not exist|doesn.t exist|no such|not found|absent|never written|"
    r"never existed|fabricat|never built|exists anywhere|"
    r"\bno\b[^\n]{0,120}\bexists\b",
    re.IGNORECASE,
)

# Path-like tokens: backticked, or bare with a source-ish extension and a
# slash. The bare branch excludes glob/template/URI/absolute shapes by
# construction (extension must be literal and known).
_SOURCE_EXTENSIONS = {
    ".py", ".ts", ".js", ".tsx", ".jsx", ".go", ".rs", ".sh",
    ".json", ".yaml", ".yml", ".sql", ".swift", ".kt", ".rb", ".lua",
}

PROSE_TOKEN_RE = re.compile(
    r"`([^`]+)`|"
    r"\b([\w./-]+\.(?:py|ts|js|tsx|jsx|go|rs|sh|json|yaml|yml|sql|swift|kt|rb|lua))\b"
)

# Shapes that are never a claim of a repo-relative file existing.
_PROSE_SKIP_PREFIXES = ("http://", "https://", "~/", "/")
_PROSE_SKIP_SUBSTRINGS = ("://", "*", "{", "}", "<", ">", "?")


def _normalize_prose_token(raw: str) -> str | None:
    """Return a repo-relative path candidate, or None if the token is not a
    claim of a repo file existing (absolute, URL, glob, template, traversal,
    unknown extension, no directory component)."""
    tok = raw.strip()
    if not tok:
        return None
    if tok.startswith(_PROSE_SKIP_PREFIXES):
        return None
    # A traversal reference escapes the repo root; it is not a repo-relative
    # claim. Checked BEFORE normalization (lstrip would strip the leading
    # dots and silently convert "../x.py" into a repo-relative "x.py").
    if tok.startswith(".."):
        return None
    if any(s in tok for s in _PROSE_SKIP_SUBSTRINGS):
        return None
    # A path with no directory component is a bare filename mention, not a
    # file claim with provenance (and far too common to gate on).
    if "/" not in tok:
        return None
    ext = Path(tok).suffix.lower()
    if ext not in _SOURCE_EXTENSIONS:
        return None
    # Collapse leading ./ to repo-relative.
    rel = tok.lstrip("./")
    if not rel:
        return None
    return rel


def _prose_context_negates(lines: list[str], idx: int, radius: int = 2) -> bool:
    """True when the line's immediate context explicitly negates existence.

    Radius 2 keeps negation detection tight: RECONCILIATION.md names
    core/factory.py in a sentence that later, several lines down, proves
    non-existence — but the LINE containing the token either asserts it
    exists ("core/factory.py (SovereignAgentFactory)" describing the
    fabrication) or negates it ("No core/factory.py ... exists anywhere").
    The context window must be small enough that a distant negation does
    not silence a nearby fabrication assertion.
    """
    lo = max(0, idx - radius)
    hi = min(len(lines), idx + radius + 1)
    return bool(PROSE_NEGATION_RE.search("\n".join(lines[lo:hi])))


def scan_prose_fabrication(md_file: Path, text: str, root: Path) -> list[str]:
    """Return a list of "path (line N)" strings for prose claims of
    non-existent files under a completed-verification signature.

    An empty list means the doc is fine — either it asserts no DONE state,
    declares the exempt marker, references only real files, or its missing
    references are explicitly negated.
    """
    if PROSE_EXEMPT_RE.search(text):
        return []
    if not PROSE_SIGNATURE_RE.search(text):
        return []

    lines = text.splitlines()
    findings: list[str] = []
    for idx, line in enumerate(lines):
        # Inside fenced code blocks is not prose (examples, schemas).
        for match in PROSE_TOKEN_RE.finditer(line):
            raw = match.group(1) or match.group(2)
            rel = _normalize_prose_token(raw)
            if rel is None:
                continue
            if (root / rel).is_file():
                continue
            if _prose_context_negates(lines, idx):
                continue
            findings.append(f"{rel} (line {idx + 1})")
    return findings


_EXCLUDED_FILENAMES = {"README.md", "CHANGELOG.md"}
_EXCLUDED_DIR_NAMES = {"notes", "research", "plans"}


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

    if not args.docs_root.is_dir():
        # Fail closed on a bad docs_root. Path.rglob on a missing directory
        # yields nothing, so a typo or a directory rename would otherwise
        # silently disable the gate forever with a clean exit 0. No report is
        # written: this is an invocation error, not a claim-verification
        # result. Exit 2 distinguishes it from exit 1 ("found real failures").
        print(
            f"ERROR: docs_root is not a directory: {args.docs_root}",
            file=sys.stderr,
        )
        return 2

    claims_found = 0
    implemented_count = 0
    planned_count = 0
    prose_exempt_count = 0
    claims: list[dict] = []
    failures: list[dict] = []

    root = Path.cwd().resolve()

    for md_file in find_markdown_files(args.docs_root):
        try:
            text = md_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            # An unreadable doc is an unknown evidence state, which is a
            # failure state -- not a crash that skips the report entirely.
            failures.append(
                {
                    "id": None,
                    "doc": str(md_file),
                    "error": f"could not read file: {exc}",
                    "missing_files": [],
                    "missing_tests": [],
                }
            )
            continue

        # Prose fabrication check (v0.2) -- independent of claim blocks.
        if PROSE_EXEMPT_RE.search(text):
            prose_exempt_count += 1
        prose_findings = scan_prose_fabrication(md_file, text, root)
        if prose_findings:
            failures.append(
                {
                    "id": None,
                    "doc": str(md_file),
                    "error": (
                        "prose fabrication: doc asserts a completed/verified "
                        "state while referencing non-existent files"
                    ),
                    "missing_files": prose_findings,
                    "missing_tests": [],
                }
            )

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
        "prose_exempt": prose_exempt_count,
        "claims": claims,
        "failures": failures,
    }
    _write_report(args.report_path, report)

    for failure in failures:
        print(f"FAIL {failure['doc']}: claim id={failure['id']!r}", file=sys.stderr)
        if failure["error"]:
            print(f"  {failure['error']}", file=sys.stderr)
        for path in failure["missing_files"]:
            print(f"  missing file: {path}", file=sys.stderr)
        for path in failure["missing_tests"]:
            print(f"  missing test: {path}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
