#!/usr/bin/env python3
"""Blueprint citation index — resolve a bare ``§N`` to the document that owns it.

Why this exists
---------------
This repo cites its own design documents by section number: ``§53``, ``spec
§4.2.3``, ``unified-architecture §27``, ``project-map §2``. That shorthand only
works while each number has exactly one owner. Neither half of that holds here:

* The numbered documents under ``docs/`` have overlapping ranges. §1 exists in
  ten of them, so a bare ``§1`` is not a reference — it is a guess.
* Several documents that code cites most are not in the repository at all. The
  single most-cited label is ``spec``, and the document behind it — cited as
  *Sovereign Architecture v4.0* — was not found in this repo or in the vault.

The result is citations that read as precise and resolve to nothing. PLAN.md
attributes its list of 15 governance metrics to "blueprint §53"; §53 is the
Steward blueprint's *project health vector*, and that blueprint lives in the
vault, not here.

What this generates
-------------------
``docs/blueprint-index.md``, in four parts:

1. **Citation vocabulary** — every label that appears before a ``§`` in the
   tree, mapped to the document that owns it, whether that document is readable
   from a checkout, and how many citations depend on it.
2. **Numbered documents in this repository** — discovered by structure.
3. **Number collisions** — the numbers that more than one in-repo document
   owns, i.e. the ones that must never be written bare.
4. **Citations that do not resolve** — unresolved, ambiguous, and
   external-only, each with file:line.

Usage
-----
    python3 scripts/blueprint_index.py              # print the index
    python3 scripts/blueprint_index.py --write      # write docs/blueprint-index.md
    python3 scripts/blueprint_index.py --check      # exit 1 if the index has drifted
    python3 scripts/blueprint_index.py --citations  # per-citation file:line detail

Determinism, and its limit
--------------------------
Output is a pure function of the files in this repo. External documents are
rendered from descriptors below and never read from disk, so a foreign checkout
produces a byte-identical index — which also means CI cannot re-verify an
external document's numbering. Each descriptor records when a human last checked
it against the real file. Treat an unverified descriptor as a lead, not a fact.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "docs" / "blueprint-index.md"

# Directories holding generated output or vendored trees, never authored docs.
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".build",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
    "var",
    "runtime",
    "artifacts",
    "ai-workspace",
    "ai-wworkspace",
}

SCAN_SUFFIXES = {".md", ".py"}

# Files whose § characters are examples or output rather than citations, and
# which must therefore not be scanned:
#   - the generator's own docstring (worked examples),
#   - the index it writes (its tables are nothing but § references),
#   - the test file that pins the generator (worked examples again).
# Scanning the second would make the index non-idempotent: every write would add
# citations and change its own input. Scanning the other two would file a pile of
# fictional citations against real documents.
EXCLUDED_SOURCES = {
    "scripts/blueprint_index.py",
    "docs/blueprint-index.md",
    "tests/docs/test_blueprint_index.py",
}

# "## 7. LAYER 02 — ..." / "### 4.2 Love Gateway" / "# 53. X"
HEADING_RE = re.compile(r"^(#{1,4})[ \t]+(?:§[ \t]*)?(\d+(?:\.\d+)*)[.)]?[ \t]+(\S.*?)[ \t]*$")

# "§53", "§ 4.2.3", "§27-28" (the range is captured as its first number).
CITATION_RE = re.compile(r"§[ \t]*(\d+(?:\.\d+)*)")

# Numbered headings a file needs before it counts as owning a scheme.
MIN_SECTIONS = 3

# Above this many owners, a collision row lists a count instead of every path.
LIST_LIMIT = 6

# How many candidate owners a per-citation row prints before eliding the rest.
CANDIDATES_SHOWN = 3

# How far back from a citation to look for a document label on the same line.
ANCHOR_WINDOW = 160

# How many leading lines count as a file's "header" when looking for the document
# it declares. Module docstrings and file banners sit here; 30 lines covers the
# longest one in this tree with room to spare.
HEADER_LINES = 30

# A label that genuinely may mean more than one document. Resolving it would be
# worse than reporting it.
AMBIGUOUS = "?ambiguous"


@dataclass(frozen=True)
class Owner:
    """A document that owns a numbering scheme."""

    doc_id: str
    label: str
    location: str  # "repo" | "vault" | "unfound"
    locator: str
    numbering: str
    sections: tuple[tuple[str, str], ...]
    verified: str
    note: str = ""

    @property
    def readable(self) -> bool:
        return self.location == "repo"


OWNERS: tuple[Owner, ...] = (
    Owner(
        doc_id="sovereign-architecture-v4",
        label="Sovereign Architecture v4.0 — cited as *spec*",
        location="unfound",
        locator="not in this repo; not found in ~/Documents/Vault either",
        numbering="unattested — the document could not be located",
        sections=(),
        verified="2026-09-22: repo-wide and vault-wide search, no match",
        note=(
            "The most-cited label in the tree after `blueprint`. Owns §4.2.1 "
            "(codegraph), §4.2.2 (memory fabric), §4.2.3 (context engine). "
            "`docs/audits/forensic-build-audit-2026-08-15.md` gives the title "
            "'Sovereign Architecture v4.0'. `~/Documents/Vault/30_Architecture/"
            "Sovereign_Recursive_Agent_Architecture.md` is the nearest candidate "
            "but is SRA-001 with a different scheme (§4.2 there is 'Love "
            "Gateway'), so it is not this document."
        ),
    ),
    Owner(
        doc_id="unified-architecture",
        label="Unified Architecture — cited as *unified-architecture*",
        location="unfound",
        locator="not in this repo; the only vault hit is a prose mention",
        numbering="unattested — no located document has the cited §5-§31",
        sections=(),
        verified="2026-09-22: repo search + vault search",
        note=(
            "Cited for §5, §6, §7, §13, §14, §17, §27, §28, §31. Tested and "
            "rejected the obvious hypothesis that these are Steward blueprint "
            "numbers: Steward §27 is 'COGNITIVE CACHE POLICY' and §28 is 'MODEL "
            "ROUTING ENGINE', while `src/msb_v3/tasks/models.py` calls §27 the "
            "unified task object. `~/Documents/Vault/10_Projects/msb-v3/MSB-v3.md` "
            "contains the phrase but has no numbered headings."
        ),
    ),
    Owner(
        doc_id="meta-system-blueprint",
        label="Meta-System blueprint — cited as *Meta-System blueprint*",
        location="unfound",
        locator="not in this repo; no vault match",
        numbering="unattested — cited for §5, §6, §7, §13, §14, §15",
        sections=(),
        verified="2026-09-22: repo search + vault search",
        note=(
            "Cited 19 times, all in `src/msb_v3/meta/contracts.py`, which treats "
            "it as the authority for the task state machine, task graph, Model "
            "Task Language, failure compiler, recursive decomposition and "
            "difficulty routing. The module implements those contracts with no "
            "readable source to check them against."
        ),
    ),
    Owner(
        doc_id="north-star-blueprint",
        label="North Star / Dream Big Blue blueprint",
        location="unfound",
        locator=(
            "in-repo candidate `docs/blueprints/2026-09-09-production-hardening-"
            "north-star.md` has unnumbered headings"
        ),
        numbering="unattested — cited for §20, §25, §27",
        sections=(),
        verified="2026-09-22: repo search + vault search",
        note=(
            "`src/msb_v3/agent/handle.py` is 'the Dream Big Blue vertical slice "
            "(North Star, blueprint §20)'. The in-repo north-star document uses "
            "titled headings, not numbers, so its sections cannot be cited this "
            "way. `~/Documents/Vault/10_Projects/Dream-Big-Blue.md` also has no "
            "numbered headings."
        ),
    ),
    Owner(
        doc_id="steward-blueprint",
        label="Steward blueprint (AIL-MoIE Project Steward)",
        location="vault",
        locator="~/Documents/Vault/30_Architecture/AIL-MoIE-Project-Steward/00_Blueprint-V2.md",
        numbering="91 numbered sections",
        sections=(
            ("7", "LAYER 02 — CANONICAL PROJECT STATE"),
            ("52", "CAPABILITY BOUNDARIES"),
            ("53", "PROJECT HEALTH VECTOR"),
            ("54", "UNKNOWN ≠ GREEN"),
        ),
        verified="2026-09-22, read directly",
        note=(
            "The real owner of §53 and §54. `src/msb_v3/steward/state.py` "
            "implements it and `docs/SURFACE.md` cites it, but nothing readable "
            "from a checkout can resolve those numbers — which is how PLAN.md "
            "came to attribute a list of governance metrics to 'blueprint §53'."
        ),
    ),
    Owner(
        doc_id="wrongness-blueprint-aib-001",
        label="Wrongness Engine blueprint AIB-001",
        location="vault",
        locator="~/Documents/Vault/30_Architecture/Wrongness-Engine/04_Blueprint-AIB-001.md",
        numbering="25 numbered sections",
        sections=(),
        verified="2026-09-22, read directly",
        note=(
            "Owner of `AIB-001 §5`, cited by the P0.1 secret-prevention gate "
            "(`scripts/scan-secrets.py`, `tests/test_secret_scan.py`). Its own "
            "internal cross-reference style is `SPEC §VII / doc §X`, so even it "
            "cites a second, separate document by number."
        ),
    ),
    Owner(
        doc_id="guardian-doc-1",
        label="S-AOS Guardian — cited as *doc 1*",
        location="vault",
        locator="~/Documents/Vault/30_Architecture/S-AOS-Guardian/blueprints/2026-08-31_blueprint-01.md",
        numbering="unverified — the file was located by name only",
        sections=(),
        verified="2026-09-22: located by name, numbering not confirmed",
        note=(
            "Cited for §4, §7, §10, §12. The mapping from the label `doc 1` to "
            "this file is an inference from the directory listing and should be "
            "confirmed before being relied on."
        ),
    ),
    Owner(
        doc_id="guardian-doc-4",
        label="S-AOS Guardian — cited as *doc 4*",
        location="unfound",
        locator="no `blueprint-04` file exists in the S-AOS-Guardian directory",
        numbering="unattested",
        sections=(),
        verified="2026-09-22: directory listing checked",
        note=(
            "Cited for §4, §5, §6, §7 by every module under "
            "`src/msb_v3/guardian/`. The directory holds `blueprint-01` and "
            "`blueprint-02`, so the label does not map onto the filenames."
        ),
    ),
    Owner(
        doc_id="doctoral-research-blueprint",
        label="MSB-v3 Doctoral Research Blueprint",
        location="vault",
        locator="~/Documents/Vault/10_Projects/msb-v3/MSB-v3-Doctoral-Research-Blueprint.md",
        numbering="27 numbered sections",
        sections=(
            ("2", "Prime Directive — the seven questions every proposed job must answer"),
            ("3", "Central Research Object — Authority–Intelligence Separation (the RQ)"),
            ("23", "Independent Review Gate"),
            ("24", "Research Green-Gate"),
            ("25", "Do NOT Build in This Phase"),
            ("26", "Master Loop"),
            ("27", "First Five Deliverables (nothing else until green)"),
        ),
        verified="2026-09-22, read directly",
        note=(
            "The blueprint `research/PLAN.md` cites without naming. §2 holds the seven "
            "questions and §25 lists exactly what `research/PLAN.md` says it forbids "
            "(no UI, SaaS, feature work, extra orchestration), which is what pins the "
            "attribution. `docs/blueprints/governance-hardening.md` reported the same "
            "document as unrecoverable."
        ),
    ),
    Owner(
        doc_id="sovereign-agentic-runtime-build-spec",
        label="Sovereign-Agentic-Runtime-Build-Spec v1",
        location="vault",
        locator="~/Documents/Vault/30_Architecture/Sovereign-Agentic-Runtime-Build-Spec-v1.md",
        numbering="18 numbered headings (§0-§9, with §3.1-§3.5)",
        sections=(
            ("3.1", "Task"),
            ("3.2", "Event (append-only, hash-chained)"),
            ("3.3", "Taint label"),
            ("3.4", "Verification Receipt"),
            ("3.5", "Router Decision"),
            ("4", "Event Contract"),
            ("5", "Invariants (enforced in deterministic code, tested)"),
            ("6", "Build Order"),
        ),
        verified="2026-09-22, read directly",
        note=(
            "The owner of `§3.4`, cited by `src/msb_v3/agent/verify.py` as the shape of "
            "every verification receipt. This is the third document the label *spec* "
            "has been used for, alongside Sovereign Architecture v4.0 and Paseo's "
            "protocol spec."
        ),
    ),
    Owner(
        doc_id="deliverable-02",
        label="Deliverable 02 (identity shadow)",
        location="vault",
        locator="~/Documents/Vault/10_Projects/msb-v3/Del02-Open-Questions-PROPOSED.md",
        numbering="no numbered headings — §10 cannot be resolved",
        sections=(),
        verified="2026-09-22, read directly",
        note=(
            "Cited for §10 by `src/msb_v3/governance/identity_shadow.py`, "
            "`tools/runtime.py` and `harnesses/base.py` — the shadow-mode "
            "deliverable. Only a PROPOSED open-questions file was found, with no "
            "numbered sections."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Citation vocabulary
#
# Every label that appears immediately before a "§" in this tree, mapped to the
# document it names. Hand-authored on purpose: deriving it from filenames is how
# you end up resolving 60 bare "blueprint §N" citations to whichever file
# happens to be called blueprint.md. Verified 2026-09-22 against the label
# counts emitted by `--vocabulary`.
# ---------------------------------------------------------------------------
ANCHORS: tuple[tuple[str, str], ...] = (
    # External owners, longest label first so specific beats generic.
    ("ail–moie steward blueprint", "steward-blueprint"),
    ("ail-moie steward blueprint", "steward-blueprint"),
    ("steward blueprint", "steward-blueprint"),
    ("sovereign architecture v4.0", "sovereign-architecture-v4"),
    ("sovereign-architecture", "sovereign-architecture-v4"),
    ("sovereign architecture", "sovereign-architecture-v4"),
    ("meta-system blueprint", "meta-system-blueprint"),
    ("wrongness-engine blueprint", "wrongness-blueprint-aib-001"),
    ("aib-001", "wrongness-blueprint-aib-001"),
    ("unified-architecture", "unified-architecture"),
    ("unified architecture", "unified-architecture"),
    ("deliverable 02", "deliverable-02"),
    ("deliverable-2", "deliverable-02"),
    ("del02", "deliverable-02"),
    ("doctoral research blueprint", "doctoral-research-blueprint"),
    ("research blueprint", "doctoral-research-blueprint"),
    ("sovereign-agentic-runtime-build-spec", "sovereign-agentic-runtime-build-spec"),
    ("build-spec", "sovereign-agentic-runtime-build-spec"),
    ("north star, blueprint", "north-star-blueprint"),
    ("north-star blueprint", "north-star-blueprint"),
    ("dream big blue", "north-star-blueprint"),
    ("doc 1", "guardian-doc-1"),
    ("doc 4", "guardian-doc-4"),
    # In-repo documents, named as the tree names them.
    ("convergence blueprint", "docs/blueprints/convergence-to-12/blueprint.md"),
    ("convergence-to-12", "docs/blueprints/convergence-to-12/blueprint.md"),
    # The tree also names some documents by their dated filename stem, which is how
    # `docs/blueprints/plans/*` headers introduce the blueprint they implement. The
    # bare hyphenated token cannot match there: in
    # `2026-08-11-adaptive-build-environment.md` the character before it is a `-`,
    # which the word-boundary pattern rejects. The dated form is what those files
    # actually write, so it is a label, and it is registered as one.
    (
        "2026-08-11-adaptive-build-environment",
        "docs/blueprints/2026-08-11-adaptive-build-environment.md",
    ),
    (
        "2026-09-09-production-hardening",
        "docs/blueprints/2026-09-09-production-hardening.md",
    ),
    ("production-hardening blueprint", "docs/blueprints/2026-09-09-production-hardening.md"),
    ("production hardening blueprint", "docs/blueprints/2026-09-09-production-hardening.md"),
    ("governance-hardening", "docs/blueprints/governance-hardening.md"),
    ("adaptive-build-environment", "docs/blueprints/2026-08-11-adaptive-build-environment.md"),
    ("m1-governance-node-architecture", "docs/blueprints/plans/m1-governance-node-architecture.md"),
    ("m1-core-loop", "docs/blueprints/convergence-to-12/M1-core-loop.md"),
    ("live-loop-composition-plan", "docs/blueprints/convergence-to-12/live-loop-composition-plan.md"),
    ("forensic-grill", "docs/audits/forensic-grill-2026-09-02.md"),
    # The tree writes the same document with a space (`Forensic grill §8`) in the
    # reconstructing blueprint. Both are labels; the hyphenated form is not a
    # superset because the word-boundary pattern rejects the space.
    ("forensic grill", "docs/audits/forensic-grill-2026-09-02.md"),
    ("forensic-build-audit", "docs/audits/forensic-build-audit-2026-08-15.md"),
    ("project-map", "docs/project-map.md"),
    # The tree writes this one as a full path, so the token must match the path.
    ("task-contract-v1.md", "docs/task-contract-v1.md"),
    ("task-contract-v1", "docs/task-contract-v1.md"),
    ("task-contract", "docs/task-contract-v1.md"),
    # Deliberately last, and deliberately unresolvable. Both labels are so
    # generic that they have been used for several different documents, and
    # guessing a referent would hide the problem this index exists to expose.
    ("spec", AMBIGUOUS),
    ("blueprint", AMBIGUOUS),
)

ANCHOR_IDS = frozenset(doc_id for _, doc_id in ANCHORS)
AMBIGUOUS_TOKENS = frozenset(token for token, doc_id in ANCHORS if doc_id == AMBIGUOUS)
OWNER_IDS = frozenset(owner.doc_id for owner in OWNERS)


@dataclass
class Section:
    number: str
    title: str
    line: int


@dataclass
class Document:
    doc_id: str
    path: str
    sections: list[Section]

    @property
    def numbers(self) -> list[str]:
        return [s.number for s in self.sections]

    @property
    def top_level(self) -> list[str]:
        """Section numbers that are not sub-sections of another number."""
        return [n for n in self.numbers if "." not in n]

    def describe_scheme(self) -> str:
        """e.g. ``14 sections (8 top-level, 1-8)`` — sub-numbering included."""
        if not self.sections:
            return "—"
        top = sorted(self.top_level, key=int)
        span = f"{top[0]}–{top[-1]}" if top else "none"
        return f"{len(self.numbers)} sections ({len(top)} top-level, {span})"


@dataclass
class CitationSite:
    path: str
    line: int
    number: str
    label: str
    status: str
    candidates: tuple[str, ...]
    external: bool = False

    @property
    def where(self) -> str:
        return ", ".join(f"`{c}`" for c in self.candidates) or "—"


def iter_source_files() -> list[Path]:
    """Every authored .md/.py file, deterministically ordered."""
    found: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        rel = path.relative_to(REPO_ROOT)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if rel.as_posix() in EXCLUDED_SOURCES:
            continue
        found.append(path)
    return sorted(found)


def parse_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    seen: set[str] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        match = HEADING_RE.match(line)
        if not match:
            continue
        number = match.group(2)
        if number in seen:  # a re-used number can't disambiguate anything
            continue
        seen.add(number)
        sections.append(Section(number, match.group(3).strip(), lineno))
    return sections


def build_documents() -> list[Document]:
    """Numbered documents under docs/ — discovered by structure, not by list."""
    docs: list[Document] = []
    for path in iter_source_files():
        if path.suffix != ".md":
            continue
        rel = path.relative_to(REPO_ROOT)
        if rel.parts[0] != "docs":
            continue
        sections = parse_sections(path.read_text(encoding="utf-8", errors="replace"))
        if len(sections) >= MIN_SECTIONS:
            docs.append(Document(doc_id=rel.as_posix(), path=rel.as_posix(), sections=sections))
    return docs


def _label_pattern(token: str) -> re.Pattern[str]:
    """A word-boundary match for a label.

    Boundaries matter: a plain substring search for ``spec`` also matches
    ``respect``, ``specify`` and ``inspection``, which silently inflates the
    count for the most-cited label in the tree. ``doc 1`` must not match
    ``doc 10`` either.
    """
    return re.compile(rf"(?<![a-z0-9-]){re.escape(token)}(?![a-z0-9-])", re.IGNORECASE)




LABEL_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (_label_pattern(token), token, doc_id) for token, doc_id in ANCHORS
)


def label_for(window: str, *, confine: bool = True) -> tuple[str, str] | None:
    """The (label text, owner doc id) nearest the citation in ``window``, if any.

    ``confine`` limits the search to the citation's own clause, which is the
    tightest reading: in "the spec is elsewhere. See §5" the spec is not what §5
    refers to. Without it the whole line is searched, which is the reading that
    resolves a table row like *"(Steward blueprint Layer 02) ... enforces §53"*
    — the label is in an earlier cell, but the row is still about the Steward
    blueprint.

    Nearest wins; ANCHORS order breaks ties on specificity, so
    ``convergence blueprint`` beats ``blueprint``.
    """
    if confine:
        window = re.split(r"[|;]|\.\s|:\s", window)[-1]
    best: tuple[int, int, str, str] | None = None
    for order, (pattern, token, doc_id) in enumerate(LABEL_PATTERNS):
        matches = list(pattern.finditer(window))
        if not matches:
            continue
        last = matches[-1]
        # Rank by position first, then by specificity: an earlier ANCHORS entry
        # (longer, more specific label) wins when two end at the same offset.
        rank = (last.end(), -order)
        if best is None or rank > (best[0], best[1]):
            best = (rank[0], rank[1], doc_id, token)
    return (best[3], best[2]) if best else None


def _label_docs_in(text: str) -> set[str]:
    """Every document label present in ``text``, not only the nearest one."""
    return {doc_id for pattern, _token, doc_id in LABEL_PATTERNS if pattern.search(text)}


def file_default_label(path: Path) -> str | None:
    """The single document a file's header declares, if it declares exactly one.

    Files here name their source document once at the top and then cite it by
    bare number for another thousand lines. Reading only the citation's own line
    loses that, and the number then resolves to whichever in-repo document
    happens to own it — which is how `tasks/events.py`'s §28 came to be
    attributed to a forensic audit about disk space.

    The whole header is read, not just the clause next to a `§`. `src/msb_v3/
    steward/__init__.py` names the Steward blueprint in line 1 and then says "the
    blueprint's core rules" twenty characters before its first citation, so a
    clause-level reading would pick up the generic back-reference and let it veto
    the specific name that the file actually declared.

    Two things still make a header declare nothing:

    * every label in it is generic (`spec`, `blueprint`) — those name no document;
    * it names more than one specific document — then there is no single default.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    header = "\n".join(text.splitlines()[:HEADER_LINES])
    declared: set[str] = set()
    for match in CITATION_RE.finditer(header):
        declared |= _label_docs_in(header[: match.start()])
    specific = declared - {AMBIGUOUS}
    if len(specific) == 1:
        return specific.pop()
    return None


def _owner_readable(doc_id: str) -> bool:
    return not any(o.doc_id == doc_id and not o.readable for o in OWNERS)


def resolve(
    number: str,
    label: str | None,
    docs: list[Document],
    file_default: str | None = None,
    line_label: str | None = None,
    own_doc: str | None = None,
) -> tuple[str, list[str], bool]:
    """Resolve one citation to (status, candidate doc ids, target-is-external).

    Precedence is by how much the citation itself says, not by how confident a
    guess looks:

    1. an inline label — `spec §4.2.3`, `unified-architecture §27`;
    2. a specific label elsewhere on the citation's line;
    3. the document the file's own header declares;
    4. the file's own document, when the number is one of its headings;
    5. a bare number that exactly one in-repo document owns;
    6. a bare number several own — reported, never guessed.
    """
    if label == AMBIGUOUS:
        return "ambiguous-label", [AMBIGUOUS], False
    if label:
        return "anchored", [label], not _owner_readable(label)
    # A specific label elsewhere on the same line, in an earlier clause. Weaker
    # than the citation's own words, stronger than the file's header. A generic
    # label here is ignored rather than allowed to swallow the file default: a
    # stray "spec" on the line says nothing.
    if line_label and line_label != AMBIGUOUS:
        return "same-line", [line_label], not _owner_readable(line_label)

    if file_default is not None:
        owner = next((o for o in OWNERS if o.doc_id == file_default), None)
        if owner is not None:
            known = {n for n, _ in owner.sections}
            # An unlocated document has no numbering to check against, so the
            # header's claim is the best evidence there is.
            if not known or number in known:
                return "file-default", [file_default], not owner.readable
        else:
            doc = next((d for d in docs if d.doc_id == file_default), None)
            # An in-repo document has a known scheme, so the claim is checkable
            # and a mismatch means the header is not talking about this number.
            if doc is not None and number in doc.numbers:
                return "file-default", [file_default], False

    # Dotted numbers need no special case. Several documents here use sub-numbering
    # (§3.1-§3.6 in the production-hardening blueprint, §1.1-§1.4 in the forensic
    # grill), so assuming a dot meant "external" made them unresolvable by mistake.
    owners = [doc.doc_id for doc in docs if number in doc.numbers]
    if len(owners) == 1:
        # The only document that owns this number is the one the citation sits in.
        # That is the single-candidate lead plus one more fact — enough to say the
        # number is the file's own, and not enough to skip the caveat below, so it
        # is reported under its own status rather than as a named citation.
        # Inferring self-reference from "the file has a §N heading" alone is not
        # usable: `docs/blueprints/governance-hardening.md` has its own §8 and also
        # cites the *forensic grill's* §8, and only the ownership test tells them
        # apart.
        if own_doc is not None and owners[0] == own_doc:
            return "own-section", owners, False
        return "single-candidate", owners, False
    if len(owners) > 1:
        return "ambiguous", owners, False
    for owner in OWNERS:
        if number in {n for n, _ in owner.sections}:
            return "single-candidate", [owner.doc_id], True
    return "unresolved", [], False


def collect_sites(docs: list[Document]) -> list[CitationSite]:
    sites: list[CitationSite] = []
    doc_ids = {doc.doc_id for doc in docs}
    for path in iter_source_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        file_default = file_default_label(path)
        # A file that is itself a discovered document can cite its own sections.
        own_doc = rel if rel in doc_ids else None
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in CITATION_RE.finditer(line):
                number = match.group(1)
                start = max(0, match.start() - ANCHOR_WINDOW)
                found = label_for(line[start : match.start()])
                label, doc_id = found if found else ("(bare)", None)
                loose = label_for(line[start : match.start()], confine=False)
                line_label = loose[1] if loose else None
                status, candidates, external = resolve(
                    number, doc_id, docs, file_default, line_label, own_doc
                )
                sites.append(
                    CitationSite(
                        path=rel,
                        line=lineno,
                        number=number,
                        label=label,
                        status=status,
                        candidates=tuple(candidates),
                        external=external,
                    )
                )
    return sites


def number_key(number: str) -> list[int]:
    return [int(part) for part in number.split(".")]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def label_counts(sites: list[CitationSite]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for site in sites:
        counts[site.label] = counts.get(site.label, 0) + 1
    return counts


def render_vocabulary(docs: list[Document], sites: list[CitationSite]) -> list[str]:
    counts = label_counts(sites)
    lines = [
        "## Citation vocabulary",
        "",
        "Every label that appears immediately before a `§` in this tree, and the",
        "document it names. **Readable from a checkout?** is the column that decides",
        "whether a citation is something a reader can follow. Anything whose owner is",
        "*not found* or *external* is a citation no reader of this repository can follow.",
        "",
        "| Label | Owner | Where it lives | Sections | Cites | Readable from a checkout? |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]

    by_id = {doc.doc_id: doc for doc in docs}
    rows: list[str] = []
    for token, doc_id in ANCHORS:
        cites = counts.get(token, 0)
        owner = next((o for o in OWNERS if o.doc_id == doc_id), None)
        if owner is not None:
            says = {
                "repo": "yes",
                "vault": "no — external",
                "unfound": "**no — not found**",
            }[owner.location]
            rows.append(
                f"| `{token}` | {owner.label} | {owner.location} | {owner.numbering} "
                f"| {cites} | {says} |"
            )
        elif doc_id in by_id:
            doc = by_id[doc_id]
            rows.append(
                f"| `{token}` | `{doc.path}` | repo | {doc.describe_scheme()} | {cites} | yes |"
            )
    unresolvable = sum(count for token, count in counts.items() if token in AMBIGUOUS_TOKENS)
    rows.append(
        f"| *any ambiguous label* | **cannot be resolved** — see the two notes below | — | — "
        f"| {unresolvable} | no |"
    )
    lines.extend(rows)
    lines.append("")
    lines.append("### `blueprint` — a label, not a document")
    lines.append("")
    lines.append(
        "At least five documents in this tree are called a blueprint — Steward,"
    )
    lines.append(
        "Meta-System, North Star, the convergence blueprint at"
    )
    lines.append(
        "`docs/blueprints/convergence-to-12/blueprint.md`, and the plan documents"
    )
    lines.append(
        "under `docs/blueprints/` — and their ranges overlap completely. Every"
    )
    lines.append(
        "citation written as `blueprint §N` has to be judged on its context."
    )
    lines.append("")
    lines.append("### `spec` — the same problem, five times over")
    lines.append("")
    lines.append(
        "`spec` is the most-cited label in the tree and refers to at least five"
    )
    lines.append("different documents:")
    lines.append("")
    lines.append(
        "| Reading | Evidence | Verdict |"
    )
    lines.append("| --- | --- | --- |")
    lines.append(
        "| Sovereign Architecture v4.0 | §4.2.1/§4.2.2/§4.2.3 in "
        "`agent/handle.py` and `docs/SURFACE.md`; the build audit names the title | "
        "the only reading with a §4.2.x scheme — but the document is not locatable |"
    )
    lines.append(
        "| the Paseo MCP protocol spec | `docs/paseo-adapter-v1.md` cites `spec §7` "
        "and `spec §32`, and its own numbering stops at §11 | external third party |"
    )
    lines.append(
        "| the conversation producer spec | `scripts/probe_conversation_e2e.py`: "
        "`producer spec §11` | probably `docs/conversation-ledger-producer-v1.md` |"
    )
    lines.append(
        "| the conversation E2E harness spec | `tests/test_probe_self_test.py`: "
        "`spec §7` | probably `docs/conversation-e2e-harness-v1.md` |"
    )
    lines.append(
        "| the kernel spec | a job output under the gitignored `ai-workspace/` | "
        "not in this tree |"
    )
    lines.append("")
    lines.append(
        "Because a bare `spec` cannot choose between these, it is reported as an "
    )
    lines.append(
        "ambiguous label. Write the document name instead: the qualified forms "
    )
    lines.append("`sovereign-architecture` and `sovereign architecture v4.0` do resolve.")
    lines.append("")
    return lines


def render_repo_documents(docs: list[Document], sites: list[CitationSite]) -> list[str]:
    """Per-document citation load, split by how well the citation identifies it."""
    named: dict[str, int] = {}
    inherited: dict[str, int] = {}
    own: dict[str, int] = {}
    sole: dict[str, int] = {}
    contested: dict[str, int] = {}
    for site in sites:
        for candidate in site.candidates:
            if site.status in {"anchored", "same-line"}:
                named[candidate] = named.get(candidate, 0) + 1
            elif site.status == "file-default":
                inherited[candidate] = inherited.get(candidate, 0) + 1
            elif site.status == "own-section":
                own[candidate] = own.get(candidate, 0) + 1
            elif site.status == "single-candidate":
                sole[candidate] = sole.get(candidate, 0) + 1
            elif site.status == "ambiguous":
                contested[candidate] = contested.get(candidate, 0) + 1

    lines = [
        "## Numbered documents in this repository",
        "",
        "Discovered by structure: a document qualifies when it has at least "
        f"{MIN_SECTIONS} numbered headings and lives under `docs/`. The count columns",
        "separate citations that *name* the document from ones that merely land in its",
        "range:",
        "",
        "- **Named** — the citation, or its line, writes this document's name. Its,",
        "  to the same standard as the `anchored` and `same-line` statuses.",
        "- **File default** — the citation is bare, but the file it sits in names this",
        "  document in its header. Probably its, and checkable when the document is here.",
        "- **Own section** — bare, and the number is a heading of the document the",
        "  citation sits in. The one bare form that can only mean itself.",
        "- **Sole candidate** — a bare `§N` that only this document owns. Probably its,",
        "  subject to the caveat above.",
        "- **Contested** — a bare `§N` this document shares with others. Unknown which.",
        "",
        "| Document | Numbering | Named | File default | Own section | Sole candidate | Contested |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for doc in sorted(docs, key=lambda d: d.doc_id):
        lines.append(
            f"| `{doc.path}` | {doc.describe_scheme()} | "
            f"{named.get(doc.doc_id, 0)} | {inherited.get(doc.doc_id, 0)} | "
            f"{own.get(doc.doc_id, 0)} | {sole.get(doc.doc_id, 0)} | "
            f"{contested.get(doc.doc_id, 0)} |"
        )
    lines.append("")
    return lines


def render_collisions(docs: list[Document], sites: list[CitationSite]) -> list[str]:
    cited_numbers = sorted({site.number for site in sites}, key=number_key)
    rows: list[tuple[str, list[str]]] = []
    for number in cited_numbers:
        owners = sorted(doc.doc_id for doc in docs if number in doc.numbers)
        if len(owners) > 1:
            rows.append((number, owners))

    collide = [(n, o) for n, o in rows if len(o) > LIST_LIMIT]
    listed = [(n, o) for n, o in rows if len(o) <= LIST_LIMIT]
    safe = [n for n in cited_numbers if len([d for d in docs if n in d.numbers]) == 1]
    unowned = [n for n in cited_numbers if not [d for d in docs if n in d.numbers]]

    lines = [
        "## Number collisions among documents in this repository",
        "",
        "A bare `§N` in this repository is a coin flip for any number below §21, because",
        "nearly every document here numbers its sections from 1. This is the list to",
        "consult before writing a citation. It covers the numbers this tree actually",
        "cites, not every number in existence.",
        "",
        f"Of the {len(cited_numbers)} distinct numbers this tree cites:",
        "",
        f"- **{len(rows)}** are owned by more than one in-repo document",
        f"- **{len(safe)}** by exactly one",
        f"- **{len(unowned)}** by none — those citations can only point outside the repo",
        "",
        "### Numbers no bare citation can resolve",
        "",
        f"The {len(rows)} shared numbers. Those with more than {LIST_LIMIT} owners are",
        "summarised; the rest are listed with their owners.",
        "",
        "| § | In-repo owners | Owners |",
        "| --- | ---: | --- |",
    ]
    for number, owners in sorted(collide, key=lambda item: number_key(item[0])):
        lines.append(f"| §{number} | {len(owners)} | too many to list — see the registry |")
    for number, owners in sorted(listed, key=lambda item: number_key(item[0])):
        shown = ", ".join(f"`{o}`" for o in owners)
        lines.append(f"| §{number} | {len(owners)} | {shown} |")
    lines.append("")
    lines.append("### Numbers a bare citation resolves on its own")
    lines.append("")
    if safe:
        lines.append(
            "Owned by exactly one in-repo document: "
            + ", ".join(f"§{n}" for n in sorted(safe, key=number_key))
            + "."
        )
        lines.append("")
        lines.append(
            "Still not safe to write bare — an external document may own the same"
        )
        lines.append(
            "number, and five of them could not be checked. See `single-candidate` above."
        )
    else:
        lines.append("None — every cited number is contested.")
    lines.append("")
    return lines


def render_defects(sites: list[CitationSite]) -> list[str]:
    groups = (
        (
            "unresolved",
            "Unresolved",
            "No document owning the number was found in the repo or the vault, so the "
            "citation points at nothing that can be read from here. Check the context "
            "before treating these as defects: `§` is also how this tree cites external "
            "standards, and most of these are RFC sections — `src/msb_ledger/"
            "timestamping.py:33` is `RFC 5652 §5.4`, and `:54` is `RFC 3161 §2.4.2`. "
            "Those are correct citations to documents that were never expected to be "
            "in this repository.",
        ),
        (
            "ambiguous",
            "Ambiguous",
            "Bare `§N` where several in-repo documents own that number.",
        ),
        (
            "ambiguous-label",
            "Ambiguous label",
            "The label (`blueprint`, `spec`) names no specific document, so the number "
            "cannot be resolved even in principle.",
        ),
        (
            "external",
            "Resolves only outside the repository",
            "These resolve — to a document not in this repository. They are grouped by "
            "where the citation points, not by how it was resolved, so an inline "
            "`spec §4.2.3` and a bare `§53` that only the Steward blueprint owns both "
            "land here.",
        ),
    )
    lines = ["## Citations that do not resolve from a checkout", ""]
    for status, title, description in groups:
        rows = [s for s in sites if (s.external if status == "external" else s.status == status)]
        lines.append(f"### {title} — {len(rows)} site(s)")
        lines.append("")
        lines.append(description)
        lines.append("")
        if not rows:
            lines.append("None.")
            lines.append("")
            continue
        lines.append("| Where | § | Label | Resolved by | Resolves to |")
        lines.append("| --- | --- | --- | --- | --- |")
        for site in rows:
            if len(site.candidates) > CANDIDATES_SHOWN:
                # Listing 37 paths per row buries the signal. The full owner list
                # for a number is in the collisions table above.
                head = ", ".join(f"`{c}`" for c in site.candidates[:CANDIDATES_SHOWN])
                rest = len(site.candidates) - CANDIDATES_SHOWN
                candidates = f"{head}, … (+{rest})"
            else:
                candidates = ", ".join(f"`{c}`" for c in site.candidates) or "—"
            lines.append(
                f"| `{site.path}:{site.line}` | §{site.number} | `{site.label}` | "
                f"{site.status} | {candidates} |"
            )
        lines.append("")
    return lines


def build_index(docs: list[Document], sites: list[CitationSite]) -> str:
    totals: dict[str, int] = {}
    for site in sites:
        totals[site.status] = totals.get(site.status, 0) + 1
    unfound = [o for o in OWNERS if o.location == "unfound"]

    header = [
        "# Blueprint citation index",
        "",
        "> **Generated** by `scripts/blueprint_index.py` — do not edit by hand.",
        "> Regenerate with `python3 scripts/blueprint_index.py --write`;",
        "> `--check` fails if this file has drifted from the tree.",
        "",
        "## How to read this",
        "",
        "A `§N` citation here names a section of *some* design document, and the number",
        "alone does not say which. Ranges overlap across the documents in `docs/`, and",
        "the owners of the most-cited labels are not in this repository. Cite as",
        "`document §N` — e.g. `docs/project-map.md §2` — never a bare `§2`.",
        "",
        f"- Numbered documents under `docs/`: **{len(docs)}**",
        f"- External or unfound documents cited by number: **{len(OWNERS)}**, "
        f"of which **{len(unfound)}** could not be located at all",
        f"- `§` citations in the tree: **{len(sites)}**",
        f"  — anchored {totals.get('anchored', 0)} · "
        f"same-line {totals.get('same-line', 0)} · "
        f"file-default {totals.get('file-default', 0)} · "
        f"own-section {totals.get('own-section', 0)} · "
        f"single-candidate {totals.get('single-candidate', 0)} · "
        f"ambiguous {totals.get('ambiguous', 0)} · "
        f"ambiguous-label {totals.get('ambiguous-label', 0)} · "
        f"unresolved {totals.get('unresolved', 0)}",
        f"- Of those, **{sum(1 for s in sites if s.external)}** resolve only to a "
        "document outside this repository",
        "",
        "Statuses — how each citation was resolved, in order of how much the citation",
        "itself says:",
        "",
        "- **anchored** — it names its document inline, in its own clause",
        "  (`unified-architecture §27`).",
        "- **same-line** — the line names the document, in an earlier clause. A table",
        "  cell like *\"... (Steward blueprint Layer 02) ... enforces §53\"* reads this",
        "  way. Better than a header, weaker than the citation's own words.",
        "- **file-default** — it is bare, and the file's header names exactly one",
        "  document. Inherited context, not the citation's own words.",
        "- **own-section** — bare, and the number is a heading of the file's own",
        "  document (`§0.5` inside the document that defines §0.5).",
        "- **single-candidate** — bare, and exactly one document owns that number.",
        "- **ambiguous** — bare, and several in-repo documents own it.",
        "- **ambiguous-label** — the label (`blueprint`, `spec`) names nothing specific.",
        "- **unresolved** — no owner found anywhere.",
        "",
        "### What `single-candidate` does not mean",
        "",
        "It is not proof. Five of the cited documents are outside this repo with",
        "unattested numbering, so a number can have exactly one in-repo owner and still",
        "mean something else — and this tree contained a provable case of it:",
        "",
        "- `docs/releases/HARDENING-AUDIT.md` cited `§24` of a document it names on the",
        "  previous line: `docs/desktop-architecture.md`. The index resolved it to",
        "  `docs/audits/forensic-grill-2026-09-02.md` §24 (`DEPENDENCY FAILURE`) — a",
        "  forensic audit about dependency failure, for a claim about memory authority.",
        "- The named document has **no numbered sections at all** (its headings are",
        "  titles: `## Authority`), so the citation could not be followed anywhere.",
        "",
        "The citation was wrong and the resolution was wrong, and nothing in the number",
        "revealed either. That citation names its section now. Treat `single-candidate`",
        "as a lead to check, not an answer: every site that landed here needed the",
        "document named, not the number trusted.",
        "",
    ]
    return (
        "\n".join(
            [
                *header,
                *render_vocabulary(docs, sites),
                *render_repo_documents(docs, sites),
                *render_collisions(docs, sites),
                *render_defects(sites),
            ]
        ).rstrip()
        + "\n"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="write docs/blueprint-index.md")
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if the committed index has drifted"
    )
    parser.add_argument(
        "--citations", action="store_true", help="print every site as file:line"
    )
    parser.add_argument(
        "--vocabulary",
        action="store_true",
        help="print label counts, to audit the ANCHORS table",
    )
    args = parser.parse_args(argv)

    docs = build_documents()
    sites = collect_sites(docs)

    if args.citations:
        for site in sites:
            candidates = ", ".join(site.candidates) or "-"
            print(f"{site.path}:{site.line}\t§{site.number}\t{site.status}\t{candidates}")
        return 0

    if args.vocabulary:
        for label, count in sorted(label_counts(sites).items(), key=lambda kv: -kv[1]):
            print(f"{count:5d}  {label}")
        return 0

    rendered = build_index(docs, sites)

    if args.write:
        INDEX_PATH.write_text(rendered, encoding="utf-8")
        print(f"wrote {INDEX_PATH.relative_to(REPO_ROOT)} ({len(rendered.splitlines())} lines)")
        return 0

    if args.check:
        if not INDEX_PATH.exists():
            print(f"FAIL: {INDEX_PATH.relative_to(REPO_ROOT)} does not exist", file=sys.stderr)
            return 1
        if INDEX_PATH.read_text(encoding="utf-8") != rendered:
            print(
                f"FAIL: {INDEX_PATH.relative_to(REPO_ROOT)} has drifted from the tree. "
                "Regenerate with: python3 scripts/blueprint_index.py --write",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {INDEX_PATH.relative_to(REPO_ROOT)} matches the tree")
        return 0

    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
