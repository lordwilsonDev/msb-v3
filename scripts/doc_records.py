#!/usr/bin/env python3
"""Repository records — the claims documents make *about* this repository.

Why this exists
---------------
Every other gate here checks the tree: lint, the portable suite, the claims
table, the blueprint citation index. None checked the prose that describes the
tree — and on 2026-09-22 the record layer produced four wrong claims in one
session, three of them found only by accident:

* ``docs/releases/v0.4.2.md`` stated its tag as ``7a1ceb9``. The tag resolves
  to ``b00d206``: the commit it was cut at was re-created by the 2026-08-31
  history rewrite, which left the original as an unreachable object on the
  remote rather than deleting it.
* ``docs/releases/NEXT.md``'s state table cited two SHAs no fresh clone can
  resolve, with no note saying so.
* ``docs/what-msb-v3-is.md`` stated a scale measured one commit earlier.
* An agent-authored correction written into two release docs then claimed those
  orphaned SHAs "do not exist". They do. Only a server-side API query caught it.

That is the failure mode of an AI-written document: a confident hash or count
that was true when somebody typed it and quietly stopped being true. This gate
checks the parts of that record which are *checkable*.

What it checks
--------------
R1 **citations** — every commit-shaped token in a gated document resolves in
   this checkout, or is declared below with what it actually is. A declared row
   is pinned in both directions: an "unreachable" token that starts resolving,
   or a declared non-commit that does, is a finding.
R2 **tags** — every tag a gated document names exists, and each release
   declaration ``docs/releases/vX.Y.Z.md`` names the commit its own tag
   resolves to.
R3 **moving refs** — a line that pairs ``main`` / ``origin/main`` / ``HEAD``
   with a SHA must carry a date. A moving fact cannot be asserted undated: this
   is the class that went stale twice in one session.
R4 **counts** — declared counts match live measurement.
R5 **census hygiene** — every declared row is still cited somewhere, and every
   declared twin resolves.

Scope, and what it costs
------------------------
Gated: the documents that assert today's state and are not themselves dated —
the root state docs, ``docs/SURFACE.md``, ``docs/what-msb-v3-is.md``,
``docs/releases/NEXT.md``, the claims declaration, and every
``docs/releases/vX.Y.Z.md``. A dated document — anything under ``docs/audits/``,
``docs/blueprints/``, ``docs/plans/``, ``docs/superpowers/``, ``closer/``,
``artifacts/``, ``experiments/``, a ``*-baseline.md`` snapshot, or
``CHANGELOG.md`` — is a *record of a moment*: its SHAs and counts are history,
and demanding they resolve would be false precision. The report prints the
outside-gate file count and why each is out, so the boundary stays visible
instead of becoming a quiet exemption.

Honest limits
-------------
* A citation that *resolves* is not thereby current. This cannot tell a stale
  ancestor SHA from HEAD; only R3's date requirement touches that class.
* A commit SHA that is all digits and shorter than ten characters would read as
  a number here, because bare numbers in prose are dates and counts, not
  citations. No such citation exists in the tree today.
* The count claims are self-referential: the `docs/` figures include the
  document that states them, so any docs edit anywhere moves the number it is
  checked against. That is the point — a published count has to keep being true
  — but it does mean a docs-only commit may need the block updated with it.
* Prose is not checked — only the facts a document states as checkable.
* The current baseline, verified 2026-09-22: every hex token in this repo's
  markdown is either a real commit (reachable here, or present-but-unreachable
  on the remote), a run digest, a ruleset/Telegram id, or a commit belonging to
  another repository. There is no invented SHA in the tree. This gate exists to
  keep that true.

Usage
-----
    python3 scripts/doc_records.py                # check (offline, default)
    python3 scripts/doc_records.py --online       # + prove orphans and run ids
    python3 scripts/doc_records.py --census       # propose a row for anything new
    python3 scripts/doc_records.py --release v0.5.0   # machine facts for a declaration
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------
# Scope: who is gated, and why everything else is not
# --------------------------------------------------------------------------

# Documents that assert the current state of the tree.
ROOT_STATE_DOCS = ("README.md", "MANIFEST.md", "CLAUDE.md", "PLAN.md")
STATE_DOCS = (
    "docs/SURFACE.md",
    "docs/what-msb-v3-is.md",
    "docs/releases/NEXT.md",
    "docs/releases/MSB-v3-RELEASE.md",
)
# One document per released tag; its tag is immutable and its commit checkable.
DECLARATION_GLOB = "docs/releases/v[0-9]*.md"
DECLARATION_SKIP = re.compile(r"baseline\.md$")

# A dated document is a record of a moment, not a claim about today.
DATED_RECORD_DIRS = (
    "CHANGELOG.md",
    "closer",
    "artifacts",
    "experiments",
    "docs/archive",
    "docs/audits",
    "docs/blueprints",
    "docs/plans",
    "docs/superpowers",
)
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
DATED_HEADER_LINES = 8

# --------------------------------------------------------------------------
# The census: every token in a gated document that cannot resolve, and why
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Citation:
    """A commit-shaped token that does not resolve, and what it really is.

    ``unreachable`` rows are history that the 2026-08-31 rewrite left behind:
    they exist on the remote as objects no branch reaches. ``twin`` names the
    same-message commit that *is* reachable — when one exists — which is the
    difference between rewritten history and a typo. Some commits were dropped
    outright by the rewrite and so have no twin; the row records that by
    omitting it.

    The other kinds are what a hex-looking token is when it is not history:
    ``run-id`` (a GitHub Actions run, proved in ``--online``), ``digest`` (a
    content or run hash) and ``id`` (a platform id such as a ruleset or chat
    id). Only the kind decides what ``--online`` can prove.
    """

    token: str
    kind: str  # "unreachable" | "run-id" | "digest" | "id"
    what: str
    twin: str | None = None


CITATIONS: tuple[Citation, ...] = (
    Citation(
        "7a1ceb9",
        "unreachable",
        "the commit v0.4.2 was cut at and verified on (release-verify run "
        "33377564353 records it as head_sha)",
        twin="b00d206",
    ),
    Citation(
        "cb2c731",
        "unreachable",
        "the commit v0.4.1 was cut on, whose release-verify failed",
        twin="b90647d",
    ),
    Citation(
        "44b3685",
        "unreachable",
        "the H9 commit, mid-push in NEXT.md's original table",
        twin="ad91737",
    ),
    Citation(
        "68d481d",
        "unreachable",
        "the origin/main of that handoff",
        twin="612e2c8",
    ),
    Citation("33377564353", "run-id", "GitHub Actions run — v0.4.2 release-verify"),
    Citation("35765344776", "run-id", "GitHub Actions run — v0.5.0 release-verify"),
    Citation("20801997", "id", "GitHub ruleset id (release-tag-immutability)"),
    Citation("d96c8559b768", "digest", "MissionAnchor file digest, MANIFEST.md"),
)
# Not declared: docs/SURFACE.md mentions the ops Telegram chat id (8276057240) in
# prose, bare. It is a bare number, so it is not a citation and needs no row —
# which is the rule working, not a gap.

# --------------------------------------------------------------------------
# Counts a gated document declares, and how each is measured live
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CountClaim:
    doc: str
    label: str
    pattern: str  # one capture group per measure, in order
    measures: tuple[str, ...]


COUNT_CLAIMS: tuple[CountClaim, ...] = (
    CountClaim(
        "docs/what-msb-v3-is.md",
        "Python under src/",
        r"Python under `src/`:\s+\*\*([\d,]+) files / ([\d,]+) lines\*\*",
        ("src_files", "src_lines"),
    ),
    CountClaim(
        "docs/what-msb-v3-is.md",
        "Python under tests/",
        r"Python under `tests/`:\s+\*\*([\d,]+)\s*files / ([\d,]+) lines\*\*",
        ("tests_files", "tests_lines"),
    ),
    CountClaim(
        "docs/what-msb-v3-is.md",
        "Markdown under docs/",
        r"Markdown under `docs/`:\s+\*\*([\d,]+) files / ([\d,]+) lines\*\*",
        ("docs_files", "docs_lines"),
    ),
    CountClaim(
        "docs/what-msb-v3-is.md",
        "collected tests",
        r"collects \*\*([\d,]+) tests\*\* \((\d+) deselected",
        ("collected", "deselected"),
    ),
    CountClaim(
        "docs/what-msb-v3-is.md",
        "test-to-source ratio",
        r"Test source is (\d+)% the size of product source",
        ("test_to_source_ratio",),
    ),
)

# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

# A commit-shaped token: 7-40 hex characters, delimited so that identifiers
# (`tenant_live_test_1787679462`) and dates cannot match.
TOKEN_RE = re.compile(r"(?<![0-9a-zA-Z_])([0-9a-f]{7,40})(?![0-9a-zA-Z_])")
# A word that is a path or a filename is a path, not a citation.
FILEISH_RE = re.compile(
    r"/|\.(?:json|md|py|log|png|txt|yml|yaml|sh|csv|db|toml|lock|in)\b"
)
# A state-table row whose first cell is the ref: | `main` | `5120527` | ...
TABLE_REF_ROW_RE = re.compile(r"^\s*\|\s*`?(?:origin/main|main|HEAD)`?\s*\|")
# A sentence binding origin/main to a SHA: "origin/main is `5120527`".
REMOTE_REF_CLAIM_RE = re.compile(r"origin/main[^0-9a-f\n]{0,6}`?[0-9a-f]{7,40}")
TAG_RE = re.compile(r"\bv\d+\.\d+\.\d+\b")
HEX_LETTERS = "abcdef"


def gated_docs() -> tuple[Path, ...]:
    """The documents this gate is responsible for, in a stable order."""
    paths = [ROOT / rel for rel in (*ROOT_STATE_DOCS, *STATE_DOCS)]
    paths += [
        p
        for p in sorted(ROOT.glob(DECLARATION_GLOB))
        if not DECLARATION_SKIP.search(p.name)
    ]
    return tuple(paths)


def is_declaration(path: Path) -> bool:
    """One document per tag: docs/releases/vX.Y.Z.md."""
    return bool(re.fullmatch(r"v\d+\.\d+\.\d+\.md", path.name)) and path.parent == ROOT / "docs/releases"


def outside_gate_reason(path: Path) -> str:
    """Why a markdown file is not gated — reported, so the boundary is visible."""
    rel = path.relative_to(ROOT).as_posix()
    for record in DATED_RECORD_DIRS:
        if rel == record or rel.startswith(record + "/"):
            return f"dated record: {record}"
    if DECLARATION_SKIP.search(rel) and path.parent == ROOT / "docs/releases":
        return "dated record: baseline snapshot"
    head = "\n".join(
        path.read_text(encoding="utf-8", errors="replace").splitlines()[:DATED_HEADER_LINES]
    )
    dated = DATE_RE.search(head)
    if dated:
        return f"dated record: owns the date {dated.group(0)}"
    return "documentation: undated, makes no claim about the current tree"


def citations_in(text: str) -> list[tuple[int, str, str]]:
    """Every commit-shaped citation in a document: (line number, line, token).

    A token counts as a citation when it is backticked, or when it carries a hex
    letter. Backticks matter both ways: the docs wrap run ids and channel ids in
    them, while bare numbers in prose are dates and counts.
    """
    found: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for match in TOKEN_RE.finditer(line):
            token, start, end = match.group(1), match.start(1), match.end(1)
            word_start = line.rfind(" ", 0, start) + 1
            word_end = line.find(" ", end)
            word = line[word_start : len(line) if word_end < 0 else word_end]
            if FILEISH_RE.search(word):
                continue
            backticked = line[start - 1 : start] == "`" and line[end : end + 1] == "`"
            if not backticked and not any(c in HEX_LETTERS for c in token):
                continue
            found.append((lineno, line.strip(), token))
    return found


# --------------------------------------------------------------------------
# git / remote access
# --------------------------------------------------------------------------


def label(path: Path) -> str:
    """Path as it should read in a finding: repo-relative when it is in the repo."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True
    )


def resolves(token: str) -> bool:
    return git("rev-parse", "--verify", "--quiet", f"{token}^{{commit}}").returncode == 0


def named_tags() -> dict[str, list[str]]:
    """Tags named in gated documents, with the documents that name them."""
    out: dict[str, list[str]] = {}
    for path in gated_docs():
        if not path.exists():
            continue
        for tag in sorted(set(TAG_RE.findall(path.read_text(encoding="utf-8", errors="replace")))):
            out.setdefault(tag, []).append(path.relative_to(ROOT).as_posix())
    return out


def remote_slug() -> str | None:
    """owner/repo from the origin URL, so nothing here hardcodes the owner."""
    url = git("remote", "get-url", "origin").stdout.strip()
    match = re.search(r"github\.com[:/]+([^/]+)/([^/\s]+?)(?:\.git)?$", url)
    return f"{match.group(1)}/{match.group(2)}" if match else None


def gh(*args: str) -> tuple[bool, str]:
    """Run gh and report success, so an absent or unauthenticated gh is normal."""
    try:
        proc = subprocess.run(["gh", *args], cwd=ROOT, capture_output=True, text=True)
    except FileNotFoundError:
        return False, ""
    return proc.returncode == 0, proc.stdout.strip()


# --------------------------------------------------------------------------
# Measurements for declared counts
# --------------------------------------------------------------------------


def _count_lines(paths: list[Path]) -> int:
    return sum(
        len(p.read_text(encoding="utf-8", errors="replace").splitlines()) for p in paths
    )


# pytest prints "3586/3661 tests collected (75 deselected)" when a marker deselection
# is configured, and "42 tests collected" when it is not.
_COLLECT_WITH_DESELECT = re.compile(r"\d+/(\d+) tests? collected \((\d+) deselected\)")
_COLLECT_PLAIN = re.compile(r"(\d+) tests? collected")


def parse_collection(output: str) -> dict[str, int] | None:
    """Parse pytest's collected/deselected summary line."""
    match = _COLLECT_WITH_DESELECT.search(output)
    if match:
        return {"collected": int(match.group(1)), "deselected": int(match.group(2))}
    match = _COLLECT_PLAIN.search(output)
    if match:
        return {"collected": int(match.group(1)), "deselected": 0}
    return None


# What each measured subtree is made of: name prefix -> glob.
MEASURED_TREES = {"src": "*.py", "tests": "*.py", "docs": "*.md"}


def measure(name: str) -> int:
    """The live value of one declared count (``<tree>_<files|lines>``)."""
    if name == "test_to_source_ratio":
        # Rounded half-up, not banker's, so the documented number is stable.
        return int(measure("tests_lines") / measure("src_lines") * 100 + 0.5)
    if name in {"collected", "deselected"}:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        parsed = parse_collection(proc.stdout)
        if parsed is None:
            raise RuntimeError("could not read pytest's collection summary")
        return parsed[name]
    tree, _, kind = name.rpartition("_")
    if tree not in MEASURED_TREES or kind not in {"files", "lines"}:
        raise KeyError(name)
    paths = sorted((ROOT / tree).rglob(MEASURED_TREES[tree]))
    return len(paths) if kind == "files" else _count_lines(paths)


# --------------------------------------------------------------------------
# The checks
# --------------------------------------------------------------------------


@dataclass
class Result:
    findings: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def fail(self, path: str, message: str, lineno: int | None = None) -> None:
        where = f"{path}:{lineno}" if lineno else path
        self.findings.append(f"{where}: {message}")


def check_citations(result: Result, docs: tuple[Path, ...] | None = None) -> None:
    """R1 — resolve, or be declared."""
    declared = {row.token: row for row in CITATIONS}
    seen: set[str] = set()
    resolved = 0
    for path in docs if docs is not None else gated_docs():
        rel = label(path)
        if not path.exists():
            result.fail(rel, "gated document is missing")
            continue
        for lineno, line, token in citations_in(path.read_text(encoding="utf-8", errors="replace")):
            seen.add(token)
            if resolves(token):
                resolved += 1
                continue
            if token not in declared:
                result.fail(
                    rel,
                    f"`{token}` does not resolve here and is not declared — "
                    f"run --census, then say what it is",
                    lineno,
                )
    # Two-way pin: a declaration must stay true.
    for row in CITATIONS:
        if row.token not in seen and docs is None:
            result.findings.append(
                f"census row `{row.token}` is declared but cited by no gated document "
                f"({row.what})"
            )
        if resolves(row.token):
            result.findings.append(
                f"census row `{row.token}` is declared {row.kind} but resolves in this "
                f"checkout — the declaration is stale"
            )
        if row.twin is not None and not resolves(row.twin):
            result.findings.append(
                f"census row `{row.token}` names twin `{row.twin}`, which does not resolve"
            )
    result.stats["citations resolved"] = resolved
    result.stats["declared tokens"] = len(CITATIONS)


def check_tags(result: Result) -> None:
    """R2 — a named tag exists, and a declaration names its tag's commit."""
    for tag, docs in sorted(named_tags().items()):
        if git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}").returncode != 0:
            result.findings.append(
                f"{docs[0]}: names tag `{tag}`, which does not exist in this checkout"
            )
    checked = 0
    for path in gated_docs():
        if not is_declaration(path):
            continue
        tag = path.stem
        if git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}").returncode != 0:
            continue  # already reported above
        commit = git("rev-parse", f"refs/tags/{tag}^{{commit}}").stdout.strip()
        text = path.read_text(encoding="utf-8", errors="replace")
        checked += 1
        if commit[:7] not in text and commit not in text:
            result.findings.append(
                f"{label(path)}: is the declaration for `{tag}` but never names the "
                f"commit that tag resolves to ({commit[:7]})"
            )
    result.stats["declarations naming their tag commit"] = checked
    result.stats["tags named"] = len(named_tags())


def check_moving_refs(result: Result, docs: tuple[Path, ...] | None = None) -> None:
    """R3 — a claim about where a moving ref *is* must carry a date.

    Two structural forms only: a state-table row whose first cell is the ref,
    and a sentence that binds ``origin/main`` to a SHA. Prose that merely
    mentions a branch — "their same-message twins in `main` are ..." — makes no
    claim about where the branch is, and fenced command blocks are commands.
    """
    dated = 0
    for path in docs if docs is not None else gated_docs():
        rel = label(path)
        if not path.exists():
            continue
        in_fence = False
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            row = TABLE_REF_ROW_RE.match(line)
            remote = REMOTE_REF_CLAIM_RE.search(line)
            if not row and not remote:
                continue
            if DATE_RE.search(line):
                dated += 1
                continue
            ref = row.group(0).strip(" |`") if row else "origin/main"
            result.fail(
                rel,
                f"claims the position of `{ref}` with no date on the line — a moving "
                f"ref cannot be asserted undated",
                lineno,
            )
    result.stats["dated ref claims"] = dated


def check_counts(result: Result) -> None:
    """R4 — declared counts match live measurement."""
    checked = 0
    for claim in COUNT_CLAIMS:
        text = (ROOT / claim.doc).read_text(encoding="utf-8", errors="replace")
        match = re.search(claim.pattern, text, re.S)
        if match is None:
            result.findings.append(
                f"{claim.doc}: no longer states the {claim.label!r} this gate measures "
                f"— retire the claim or restore the sentence"
            )
            continue
        for group, name in zip(match.groups(), claim.measures):
            checked += 1
            live = measure(name)
            claimed = int(group.replace(",", ""))
            if claimed != live:
                result.findings.append(
                    f"{claim.doc}: {claim.label} claims {claimed:,} ({name}), live is {live:,}"
                )
    result.stats["declared counts checked"] = checked


SKIP_DIRS = {
    ".git", ".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".build", "build", "dist", "node_modules",
}


def markdown_files() -> list[Path]:
    """The repository's markdown: tracked files, so a staged copy agrees.

    Walking the disk instead would count untracked scratch files and make the
    boundary report differ between checkouts.
    """
    listed = git("ls-files", "*.md").stdout.split()
    if listed:
        return [ROOT / rel for rel in listed]
    return [
        path
        for path in sorted(ROOT.rglob("*.md"))
        if not SKIP_DIRS & set(path.relative_to(ROOT).parts)
    ]


def report_boundary(result: Result) -> None:
    """Say what the gate does not read, so the boundary cannot hide."""
    reasons: dict[str, int] = {}
    gated = {p.resolve() for p in gated_docs()}
    for path in markdown_files():
        if path.resolve() in gated or not path.exists():
            continue
        reason = outside_gate_reason(path).split(":")[0]
        reasons[reason] = reasons.get(reason, 0) + 1
    result.stats["markdown files outside the gate"] = sum(reasons.values())
    for reason, count in sorted(reasons.items()):
        result.notes.append(f"{count:>4}  {reason}")


def run(online: bool = False) -> Result:
    result = Result()
    if not (ROOT / ".git").exists():
        result.notes.append(
            "no .git in this checkout (staged portability copy): citation, tag and "
            "ref checks are skipped; counts still apply"
        )
    else:
        check_citations(result)
        check_tags(result)
        check_moving_refs(result)
    check_counts(result)
    report_boundary(result)
    if online:
        online_checks(result)
    return result


def online_checks(result: Result) -> None:
    """Prove the declared rows against the remote, and name each run id."""
    slug = remote_slug()
    if slug is None:
        result.notes.append("--online: no GitHub origin configured; remote checks skipped")
        return
    for row in CITATIONS:
        if row.kind == "unreachable":  # noqa: SIM102 - branch kept flat for the message
            ok, out = gh(
                "api",
                f"repos/{slug}/commits/{row.token}",
                "--jq",
                '.commit.message|split("\\n")[0]',
            )
            if not ok or not out or out.startswith('{"message"'):
                result.findings.append(
                    f"--online: census row `{row.token}` is declared unreachable but is "
                    f"not a commit on {slug} either"
                )
            elif row.twin is not None:
                twin_subject = git("log", "-1", "--format=%s", row.twin).stdout.strip()
                if twin_subject != out:
                    result.findings.append(
                        f"--online: `{row.token}` and its twin `{row.twin}` no longer share "
                        f"a message ({out!r} vs {twin_subject!r})"
                    )
    for row in CITATIONS:
        if row.kind != "run-id":
            continue
        ok, out = gh(
            "api",
            f"repos/{slug}/actions/runs/{row.token}",
            "--jq",
            '[.name, .conclusion, (.head_sha[0:7])]|join(" ")',
        )
        if not ok:
            result.findings.append(
                f"--online: census row `{row.token}` is declared a run id but is not a "
                f"run on {slug}"
            )
            continue
        result.notes.append(f"run {row.token}: {out}")
        # The declarations state these runs were green release-verify runs, so
        # check the claim rather than just the id's existence.
        fields = out.split()
        workflow, conclusion = fields[0], fields[1]
        if "release-verify" not in workflow or conclusion != "success":
            result.findings.append(
                f"--online: run `{row.token}` is {workflow} {conclusion!r}, but the "
                f"documents citing it describe a green release-verify run"
            )


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------


def census() -> int:
    """Propose a row for anything cited but undeclared — upkeep, not checking."""
    declared = {row.token for row in CITATIONS}
    pending: dict[str, list[str]] = {}
    for path in gated_docs():
        rel = path.relative_to(ROOT).as_posix()
        for lineno, _line, token in citations_in(path.read_text(encoding="utf-8", errors="replace")):
            if token not in declared and not resolves(token):
                pending.setdefault(token, []).append(f"{rel}:{lineno}")
    if not pending:
        print("[records] census: every citation is either resolved or declared")
        return 0
    print(f"[records] census: {len(pending)} undeclared token(s) — verify each, then paste:")
    for token, sites in sorted(pending.items()):
        print(f'\n    Citation(\n        "{token}",\n        "not-a-commit",  # or "unreachable"\n        "",              # what it is, if not a commit\n    ),')
        print(f"    # cited at {', '.join(sites)}")
    return 1


def release_report(tag: str, online: bool) -> int:
    """Print the facts a release declaration should carry, from git and CI."""
    commit = git("rev-parse", f"refs/tags/{tag}^{{commit}}")
    if commit.returncode != 0:
        print(f"[records] {tag} is not a tag in this checkout", file=sys.stderr)
        return 1
    sha = commit.stdout.strip()
    object_type = git("cat-file", "-t", tag).stdout.strip()
    previous = git("describe", "--tags", "--abbrev=0", f"{tag}^").stdout.strip()
    subjects = git("log", "--format=%s", f"{previous}..{sha}").stdout.splitlines()
    counts = {
        kind: sum(1 for s in subjects if s.startswith(kind))
        for kind in ("feat", "fix", "docs", "chore", "test", "refactor")
    }
    print(f"[records] {tag} → {sha[:9]}  (tag object type: {object_type})")
    print(f"  dated:            {git('log', '-1', '--format=%cs', sha).stdout.strip()}")
    print(f"  previous tag:     {previous or '(none)'} → {git('log', '-1', '--format=%h', previous).stdout.strip() if previous else '-'}")
    print(f"  commits since:    {len(subjects)}  ({counts})")
    print(f"  declaration doc:  docs/releases/{tag}.md "
          f"{'present' if (ROOT / 'docs' / 'releases' / f'{tag}.md').exists() else 'MISSING'}")
    versions = {}
    for source, pattern in (
        ("pyproject.toml", r'^version = "([^"]+)"'),
        ("src/msb_v3/__init__.py", r'__version__ = "([^"]+)"'),
        ("src/msb_v3/core/identity.py", r'version: str = "([^"]+)"'),
        ("MANIFEST.md", r"`msb-v3` `([0-9.]+)`"),
    ):
        text = (ROOT / source).read_text(encoding="utf-8", errors="replace")
        match = re.search(pattern, text, re.M)
        versions[source] = match.group(1) if match else "?"
    print(f"  version sources:  {versions}")
    if online:
        slug = remote_slug()
        if slug:
            ok, out = gh("api", f"repos/{slug}/actions/runs?head_sha={sha}&per_page=20",
                         "--jq", '.workflow_runs[]|[.name,.conclusion]|@tsv')
            print(f"  CI runs on {sha[:7]}:")
            for line in out.splitlines() if ok and out else ["    (none found)"]:
                print(f"    {line}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Repository records gate (see module docstring)")
    parser.add_argument("--online", action="store_true", help="also prove rows against the remote")
    parser.add_argument("--census", action="store_true", help="propose rows for undeclared tokens")
    parser.add_argument("--release", metavar="TAG", help="print machine facts for a tag")
    args = parser.parse_args()
    if args.census:
        return census()
    if args.release:
        return release_report(args.release, args.online)

    result = run(online=args.online)
    for note in result.notes:
        print(f"[records] {note}")
    if result.findings:
        print(f"[records] FAIL: {len(result.findings)} finding(s) in the record layer:")
        for finding in result.findings:
            print(f"  - {finding}")
        return 1
    summary = ", ".join(f"{value} {key}" for key, value in result.stats.items())
    print(f"[records] PASS: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
