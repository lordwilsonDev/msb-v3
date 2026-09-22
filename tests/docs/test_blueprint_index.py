"""The blueprint citation index is a map, so it has to be checked like one.

Two things are tested here, and they are different in kind:

* **The index is current.** `docs/blueprint-index.md` is generated from the tree,
  so a citation added anywhere under `docs/`, `src/`, `tests/` or `scripts/` can
  invalidate it. The drift test is the gate that stops the map rotting, exactly
  as `test_surface_map.py` does for `docs/SURFACE.md`.
* **The resolver cannot be fooled.** The index only earns its place if a label
  like `spec` is not matched inside the word *respect*, if `doc 1` does not
  swallow `doc 10`, and if a generic label is reported as ambiguous rather than
  silently attached to whichever document happens to have a matching number.
  Each of those was a real bug while writing the generator, so each is pinned.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts" / "blueprint_index.py"
INDEX = REPO_ROOT / "docs" / "blueprint-index.md"


def _load_generator():
    spec = importlib.util.spec_from_file_location("blueprint_index", str(GENERATOR))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["blueprint_index"] = module
    spec.loader.exec_module(module)
    return module


bi = _load_generator()


# ---------------------------------------------------------------------------
# The gate: the committed index matches the tree
# ---------------------------------------------------------------------------


def test_index_exists():
    assert INDEX.exists(), (
        "docs/blueprint-index.md is missing. Regenerate it with: "
        "python3 scripts/blueprint_index.py --write"
    )


def test_index_matches_tree():
    """The committed index is exactly what the generator produces today."""
    docs = bi.build_documents()
    rendered = bi.build_index(docs, bi.collect_sites(docs))
    on_disk = INDEX.read_text(encoding="utf-8")
    assert on_disk == rendered, (
        "docs/blueprint-index.md has drifted from the tree. "
        "Regenerate with: python3 scripts/blueprint_index.py --write"
    )


def test_generation_is_deterministic():
    docs = bi.build_documents()
    sites = bi.collect_sites(docs)
    assert bi.build_index(docs, sites) == bi.build_index(docs, sites)


def test_generator_does_not_scan_itself_or_its_output():
    """A generator that reads its own output is not idempotent.

    Both files contain § references — the docstring as examples, the index as
    its whole content — so either one being scanned would make every write
    change its own input and the index could never converge.
    """
    scanned = {path.relative_to(REPO_ROOT).as_posix() for path in bi.iter_source_files()}
    for excluded in bi.EXCLUDED_SOURCES:
        assert excluded not in scanned


# ---------------------------------------------------------------------------
# The resolver: boundaries, clause confinement, and refusal to guess
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        # Word boundaries — a substring match made these all read as "spec".
        ("respect the ", None),
        ("inspection of the ", None),
        ("this specification says ", None),
        # `spec` is a generic label, so it resolves to the ambiguous marker — the
        # index reports it rather than picking one of the five documents it names.
        ("see the spec ", ("spec", bi.AMBIGUOUS)),
        ("per SPEC ", ("spec", bi.AMBIGUOUS)),
        # `doc 1` must not swallow `doc 10`.
        ("doc 10 ", None),
        ("doc 1 ", ("doc 1", "guardian-doc-1")),
        # Specificity beats a bare label on the same line.
        (
            "convergence blueprint ",
            ("convergence blueprint", "docs/blueprints/convergence-to-12/blueprint.md"),
        ),
        ("blueprint ", ("blueprint", bi.AMBIGUOUS)),
        # Clause confinement — a label in an earlier sentence must not attach.
        ("the spec is elsewhere. See ", None),
        ("a spec it does not have; see ", None),
        # A markdown table cell boundary starts a new clause, so the label inside
        # this cell is the citation's own.
        ("prefix | the spec ", ("spec", bi.AMBIGUOUS)),
    ],
)
def test_label_matching(window, expected):
    assert bi.label_for(window) == expected


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        # Unconfined, the same windows that are bare above still resolve when the
        # label is genuinely on this line but in an earlier clause.
        (
            "(Steward blueprint Layer 02); enforces ",
            ("steward blueprint", "steward-blueprint"),
        ),
        ("the spec is elsewhere. See ", ("spec", bi.AMBIGUOUS)),
    ],
)
def test_unconfined_label_matching(window, expected):
    assert bi.label_for(window, confine=False) == expected


def test_same_line_beats_the_file_header():
    """An earlier clause on the citation's own line outranks the file's header.

    `docs/SURFACE.md` is a map of the whole repo, but its `§4.2.x` rows naming
    Sovereign Architecture v4.0 make that its header default. The steward row on
    line 114 names the Steward blueprint, so its `§53`/`§54` must stay with the
    Steward blueprint rather than inheriting the file's default.
    """
    docs = bi.build_documents()
    declared = bi.file_default_label(bi.REPO_ROOT / "docs/SURFACE.md")
    assert declared == "sovereign-architecture-v4", (
        "this header default is the whole reason the same-line tier exists"
    )
    status, candidates, external = bi.resolve("53", None, docs, declared, "steward-blueprint")
    assert status == "same-line"
    assert candidates == ["steward-blueprint"]
    assert external is True


def test_generic_same_line_label_does_not_hijack_the_file_default():
    """A stray `spec` on the line says nothing, so the header default still applies."""
    docs = bi.build_documents()
    status, candidates, _ = bi.resolve(
        "27", None, docs, "unified-architecture", bi.AMBIGUOUS
    )
    assert status == "file-default"
    assert candidates == ["unified-architecture"]


def test_bare_generic_label_is_never_guessed_into_a_document():
    """`blueprint §7` and `spec §7` must not resolve to whichever doc matches.

    This is the trap the index exists to expose: `docs/blueprints/convergence-to-12/
    blueprint.md` has the stem `blueprint`, so an alias derived from the filename
    would silently claim every bare `blueprint §N` citation in the tree.
    """
    docs = bi.build_documents()
    status, candidates, _ = bi.resolve("7", bi.AMBIGUOUS, docs)
    assert status == "ambiguous-label"
    assert candidates == [bi.AMBIGUOUS]
    # And both generic labels map to no specific document in the table.
    anchors = dict(bi.ANCHORS)
    assert anchors["blueprint"] == bi.AMBIGUOUS
    assert anchors["spec"] == bi.AMBIGUOUS


def test_dotted_numbers_resolve_to_in_repo_documents():
    """Sub-numbered sections are owned by the document, not treated as external.

    The production-hardening blueprint numbers §3.1-§3.6 and the forensic grill
    §1.1-§1.4. Assuming a dot meant "external" left nine resolvable citations
    reported as unresolved.
    """
    docs = bi.build_documents()
    status, candidates, _ = bi.resolve("3.1", None, docs)
    assert status == "single-candidate"
    assert candidates == ["docs/blueprints/2026-09-09-production-hardening.md"]


def test_external_owners_resolve_without_touching_the_filesystem():
    """Vault documents resolve from their descriptor, so a foreign checkout agrees.

    §53 and §54 belong to the Steward blueprint, which lives in the vault. If the
    generator read it, the index would differ on any machine without the vault —
    and CI could never check it.
    """
    docs = bi.build_documents()
    status, candidates, external = bi.resolve("53", None, docs)
    assert candidates == ["steward-blueprint"]
    assert external is True, "§53 resolves outside the repo and must be flagged as such"
    assert status == "single-candidate", "one candidate, and it is not in this repo"

    for owner in bi.OWNERS:
        if owner.location != "repo":
            assert not owner.readable
        if owner.location == "unfound":
            assert owner.sections == (), "an unfound document cannot have known sections"


# ---------------------------------------------------------------------------
# The file-default rule: a bare number inside a file that names its document
# ---------------------------------------------------------------------------


def test_file_default_reads_the_document_a_header_declares():
    """A module that names its source document once, then cites it bare."""
    declared = bi.file_default_label(bi.REPO_ROOT / "src/msb_v3/tasks/events.py")
    assert declared == "unified-architecture"

    # And a bare §28 in that file resolves to it, not to whichever in-repo
    # document happens to own §28.
    docs = bi.build_documents()
    status, candidates, external = bi.resolve("28", None, docs, declared)
    assert status == "file-default"
    assert candidates == ["unified-architecture"]
    assert external is True

    without = bi.resolve("28", None, docs)[1]
    assert without != candidates, (
        "the rule must be what changes the answer, not the surrounding documents"
    )


def test_file_default_is_range_checked_when_the_document_is_in_this_repo():
    """A header's claim is checkable against an in-repo document's real scheme.

    `docs/project-map.md` has 22 sections. `§99` in a file that declares it is not
    a reference to that document, so the claim is rejected rather than inherited.
    """
    docs = bi.build_documents()
    declared = "docs/project-map.md"

    inside = bi.resolve("3", None, docs, declared)
    assert inside[0] == "file-default" and inside[1] == [declared]

    outside = bi.resolve("99", None, docs, declared)
    assert outside[0] != "file-default", "§99 is outside a 22-section document"
    assert outside[0] == "unresolved"


def test_a_header_naming_only_a_generic_label_declares_nothing():
    """`spec`/`blueprint` headers must not become a file-wide default."""
    declared = bi.file_default_label(bi.REPO_ROOT / "docs/paseo-adapter-v1.md")
    assert declared is None, (
        "this header says only 'spec', which names no specific document"
    )


def test_a_generic_mention_does_not_veto_the_specific_name_in_the_same_header():
    """`the blueprint's rules` must not cancel the name given two lines above.

    This is why the header is read whole rather than clause-by-clause: the
    Steward package names its blueprint in line 1, then refers back to "the
    blueprint's core rules" within twenty characters of its first citation. A
    clause-level reading loses the name and inherits nothing.
    """
    declared = bi.file_default_label(bi.REPO_ROOT / "src/msb_v3/steward/__init__.py")
    assert declared == "steward-blueprint"

    docs = bi.build_documents()
    status, candidates, external = bi.resolve("53", None, docs, declared)
    assert status == "file-default" and candidates == ["steward-blueprint"]
    # The Steward blueprint is in the vault, so the number still cannot be
    # followed from a checkout — file-default changes the attribution, not that.
    assert external is True


def test_file_default_reads_a_document_the_tree_names_by_its_dated_filename():
    """`docs/blueprints/plans/*` introduce their blueprint by path.

    The bare token cannot match there: in `2026-08-11-adaptive-build-environment.md`
    the character before the name is a `-`, which the word-boundary pattern
    rejects, so the dated stem is registered as a label in its own right.
    """
    declared = bi.file_default_label(
        bi.REPO_ROOT / "docs/blueprints/plans/2026-08-11-phase0b-the-brakes.md"
    )
    assert declared == "docs/blueprints/2026-08-11-adaptive-build-environment.md"

    docs = bi.build_documents()
    status, candidates, _ = bi.resolve("0.5", None, docs, declared)
    assert status == "file-default"
    assert candidates == [declared]


# ---------------------------------------------------------------------------
# The own-section rule: a bare number that is one of the file's own headings
# ---------------------------------------------------------------------------


def test_own_section_resolves_a_document_citing_its_own_number():
    """`§0.5` inside the file that defines §0.5 is not a reference to anything else."""
    docs = bi.build_documents()
    own = "docs/blueprints/2026-08-11-adaptive-build-environment.md"

    status, candidates, external = bi.resolve("0.5", None, docs, None, None, own)
    assert status == "own-section"
    assert candidates == [own]
    assert external is False

    other = bi.resolve("0.5", None, docs, None, None, "docs/project-map.md")
    assert other[0] != "own-section", (
        "a file that does not own the number cannot cite it as its own"
    )


def test_own_section_is_not_inferred_from_a_heading_alone():
    """Sharing a number is not owning it — the counterexample is in this tree.

    `docs/blueprints/governance-hardening.md` has its own §8 and also cites the
    forensic grill's §8. Inferring self-reference from "this file has a §8" would
    attribute the grill's MODEL COMPROMISE section to the blueprint, which is the
    class of false resolution this index exists to stop.
    """
    docs = bi.build_documents()
    gov = "docs/blueprints/governance-hardening.md"
    grill = "docs/audits/forensic-grill-2026-09-02.md"
    assert "8" in next(d for d in docs if d.doc_id == gov).numbers
    assert "8" in next(d for d in docs if d.doc_id == grill).numbers, (
        "the counterexample only holds while both documents own §8"
    )

    status, candidates, _ = bi.resolve("8", None, docs, None, None, gov)
    assert status != "own-section"
    assert candidates != [gov]


def test_collected_sites_hand_the_own_section_rule_the_file_it_is_in():
    """The rule is wired to the scanning path, not only callable in isolation."""
    docs = bi.build_documents()
    own = {
        (site.line, site.number): site
        for site in bi.collect_sites(docs)
        if site.status == "own-section"
    }
    assert (130, "0.6") in own and (146, "0.5") in own and (196, "0.5") in own, (
        "the adaptive blueprint's three self-references are the tree's whole "
        f"own-section set; found {sorted(own)}"
    )


def test_every_anchor_label_has_a_known_owner():
    for token, doc_id in bi.ANCHORS:
        assert doc_id in bi.OWNER_IDS or doc_id == bi.AMBIGUOUS or doc_id.startswith("docs/"), (
            f"anchor {token!r} points at {doc_id!r}, which is neither a registered "
            "owner nor an in-repo document"
        )


def test_index_reports_the_citations_it_cannot_resolve():
    """The unresolved table is populated, and its numbers match the sites found."""
    titles = {
        "unresolved": "Unresolved",
        "ambiguous": "Ambiguous",
        "ambiguous-label": "Ambiguous label",
    }
    docs = bi.build_documents()
    sites = bi.collect_sites(docs)
    rendered = bi.build_index(docs, sites)
    for status, title in titles.items():
        count = sum(1 for site in sites if site.status == status)
        assert f"### {title} — {count} site(s)" in rendered, (
            f"the {status} section does not report its own count"
        )
    # The external group is keyed on where a citation points, not how it was
    # resolved, so an inline `spec §4.2.3` and a bare `§53` land together.
    external = sum(1 for site in sites if site.external)
    assert f"### Resolves only outside the repository — {external} site(s)" in rendered
    external_statuses = {site.status for site in sites if site.external}
    assert {"anchored", "file-default"} <= external_statuses, (
        "the external group must be keyed on where a citation points, not on how it "
        f"was resolved; it only contains {sorted(external_statuses)}"
    )
