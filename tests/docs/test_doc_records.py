"""The record layer is prose about a moving tree, so it gets its own gate.

Four things are tested here, and they are different in kind:

* **The gate is green.** `scripts/doc_records.py` checks the citations, tags,
  moving-ref claims and declared counts in the documents that assert the
  repository's current state. This is the drift test: if it is red, a document
  is wrong, not the test.
* **The gate can fail.** A gate that only ever passes is ceremony, so the
  fabricated-SHA, undeclared-token and undated-ref cases inject exactly the
  failures this was built for and assert the finding is produced.
* **Its scanner cannot be fooled.** Paths, identifiers and bare numbers are not
  citations — `artifacts/run-report-20260817.json` is a filename,
  `tenant_live_test_1787679462` is a collection name, `chat_id 8276057240` is a
  prose number. Each of those was a real false positive while writing the
  scanner, so each is pinned.
* **Its scope is declared.** The gate deliberately does not read dated records
  (audits, closure ledgers, baselines): their SHAs are history, and demanding
  they resolve would be false precision. The boundary is pinned here so an
  exemption cannot be widened by quietly editing a glob.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "doc_records.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("doc_records", str(SCRIPT))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["doc_records"] = module
    spec.loader.exec_module(module)
    return module


dr = _load_gate()

needs_git = pytest.mark.skipif(
    not (REPO_ROOT / ".git").exists(),
    reason="staged portability copy has no .git: citation, tag and ref checks are skipped there",
)


@pytest.fixture(scope="module")
def gate() -> dr.Result:
    """One whole run, shared: the count checks shell out to pytest."""
    return dr.run(online=False)


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "doc.md"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The gate: green on the tree as it stands
# ---------------------------------------------------------------------------


def test_gate_is_green(gate):
    assert gate.findings == [], "record-layer findings:\n  - " + "\n  - ".join(gate.findings)


def test_gate_reports_what_it_did_read(gate):
    """A gate that checks nothing must not read as a pass."""
    assert gate.stats.get("declared counts checked", 0) >= 4
    if (REPO_ROOT / ".git").exists():
        assert gate.stats.get("citations resolved", 0) > 0
        assert gate.stats.get("tags named", 0) > 0
    assert gate.stats.get("markdown files outside the gate", 0) > 0


# ---------------------------------------------------------------------------
# The census: declared in both directions
# ---------------------------------------------------------------------------


@needs_git
def test_every_citation_is_resolved_or_declared():
    """No commit-shaped token in a gated doc is unresolvable and unaccounted for."""
    declared = {row.token for row in dr.CITATIONS}
    undeclared: list[str] = []
    for path in dr.gated_docs():
        for lineno, _line, token in dr.citations_in(
            path.read_text(encoding="utf-8", errors="replace")
        ):
            if token not in declared and not dr.resolves(token):
                undeclared.append(f"{dr.label(path)}:{lineno} `{token}`")
    assert not undeclared, "unresolvable citation with no census row: " + ", ".join(undeclared)


@needs_git
def test_declared_rows_are_still_cited():
    """The other direction: a row nothing cites any more is a stale declaration."""
    cited = {
        token
        for path in dr.gated_docs()
        for _lineno, _line, token in dr.citations_in(
            path.read_text(encoding="utf-8", errors="replace")
        )
    }
    stale = [row.token for row in dr.CITATIONS if row.token not in cited]
    assert not stale, f"census rows cited by nothing: {stale}"


@needs_git
def test_unreachable_rows_still_do_not_resolve():
    """The rewrite's orphans must stay orphans; if one resolves, the note is wrong."""
    resolving = [
        row.token
        for row in dr.CITATIONS
        if row.kind == "unreachable" and dr.resolves(row.token)
    ]
    assert not resolving, (
        f"{resolving} are declared unreachable but resolve in this checkout — the "
        "documents calling them unreachable now need updating"
    )


@needs_git
def test_declared_twins_resolve():
    """A twin is the proof a row is rewritten history rather than a typo."""
    for row in dr.CITATIONS:
        if row.twin is not None:
            assert dr.resolves(row.twin), f"twin {row.twin} of {row.token} does not resolve"


def test_declared_rows_say_what_they_are():
    """The kind decides what --online can prove, so it cannot be left vague."""
    for row in dr.CITATIONS:
        assert row.kind in {"unreachable", "run-id", "digest", "id"}, row
        assert row.what.strip(), f"{row.token} is declared without saying what it is"
        if row.kind != "unreachable":
            assert row.twin is None, f"{row.token} is not history, so it has no twin"


# ---------------------------------------------------------------------------
# The gate can fail
# ---------------------------------------------------------------------------


@needs_git
def test_a_fabricated_sha_is_reported(tmp_path):
    """A token that is not history and not declared is a finding, not a pass."""
    doc = _write(tmp_path, "Fixed in `deadbeef` and pushed (see `4b8f2a1`).\n")
    result = dr.Result()
    dr.check_citations(result, docs=(doc,))
    assert len(result.findings) == 2, result.findings
    assert "deadbeef" in result.findings[0] and "not declared" in result.findings[0]


@needs_git
def test_a_real_commit_needs_no_declaration(tmp_path):
    """The same path, with a commit that exists: no finding."""
    short = dr.git("rev-parse", "--short=7", "HEAD").stdout.strip()
    doc = _write(tmp_path, f"Landed as `{short}`.\n")
    result = dr.Result()
    dr.check_citations(result, docs=(doc,))
    assert result.findings == []


@needs_git
def test_an_undeclared_token_is_reported_with_its_line(tmp_path):
    doc = _write(tmp_path, "first\n\ncited at `0000abc1`\n")
    result = dr.Result()
    dr.check_citations(result, docs=(doc,))
    assert any(":3:" in finding for finding in result.findings), result.findings


def test_a_moving_ref_claim_must_be_dated(tmp_path):
    """R3 is structural: the state-table shape is a claim, dated or it is a finding."""
    undated = _write(tmp_path, "| `main` | `5120527` — landed and pushed |\n")
    result = dr.Result()
    dr.check_moving_refs(result, docs=(undated,))
    assert len(result.findings) == 1 and "moving ref" in result.findings[0]

    dated = _write(tmp_path, "| `main` | `5120527` — landed and pushed (2026-09-22) |\n")
    result = dr.Result()
    dr.check_moving_refs(result, docs=(dated,))
    assert result.findings == []


# ---------------------------------------------------------------------------
# The scanner: what is and is not a citation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "tokens"),
    [
        # A filename is not a citation, even though it carries hex-looking digits.
        ("see `artifacts/run-report-20260817.json` for the numbers", []),
        ("`docs/releases/v0.4.2.md` names its tag", []),
        # Nor is an identifier's tail, nor a bare number in prose.
        ("`tenant_live_test_1787679462` was a stray collection", []),
        ("the home channel (chat_id 8276057240) is conf'd", []),
        ("measured 2026-08-19 and again 58,600 lines later", []),
        # A backticked number is a citation: run ids and ruleset ids live there.
        ("green from a virgin clone (CI run `35765344776`)", ["35765344776"]),
        ("ruleset `20801997` blocks tag moves", ["20801997"]),
        # A bare token with a hex letter is a citation.
        ("what `v0.4.2` resolves to *today* is b00d206", ["b00d206"]),
        # And short hex in backticks is the ordinary case.
        ("the 2026-08-31 rewrite orphaned `7a1ceb9`", ["7a1ceb9"]),
    ],
)
def test_scanning_distinguishes_citations_from_noise(line, tokens):
    assert [token for _n, _l, token in dr.citations_in(line)] == tokens


def test_scanning_reports_line_numbers():
    text = "one\ntwo\n\n`7a1ceb9` is here\n"
    assert [lineno for lineno, _line, _token in dr.citations_in(text)] == [4]


# ---------------------------------------------------------------------------
# The boundary: dated records are out, and say why
# ---------------------------------------------------------------------------


def test_scope_holds_the_documents_that_assert_state():
    gated = {dr.label(p) for p in dr.gated_docs()}
    for expected in (
        "README.md",
        "CLAUDE.md",
        "docs/what-msb-v3-is.md",
        "docs/releases/NEXT.md",
        "docs/releases/v0.5.0.md",
    ):
        assert expected in gated, f"{expected} asserts current state and must be gated"


def test_a_dated_document_is_out_of_scope_and_says_so():
    """A document that owns a date is a record; the gate must not rewrite history."""
    state = REPO_ROOT / "docs/governance/convergence-state.md"
    assert "dated record" in dr.outside_gate_reason(state), (
        "this doc carries its own date; the gate must not demand its 2026-08-28 SHAs resolve"
    )
    assert not any(p.resolve() == state.resolve() for p in dr.gated_docs())


def test_record_directories_are_out_of_scope_by_reason_not_by_glob():
    for rel in (
        "docs/audits/forensic-build-audit-2026-08-15.md",
        "closer/final/closure-audit-2026-08-30.md",
        "artifacts/core-loop/README.md",
        "docs/releases/v0.3.0-baseline.md",
        "CHANGELOG.md",
    ):
        path = REPO_ROOT / rel
        assert path.exists(), rel
        assert "dated record" in dr.outside_gate_reason(path), rel


def test_a_declaration_is_gated_even_though_it_states_a_date():
    """A release declaration names its tag, and a tag is immutable — so it is gated."""
    declaration = REPO_ROOT / "docs/releases/v0.5.0.md"
    assert declaration in dr.gated_docs(), "declarations are gated explicitly"
    assert "Dated:" in declaration.read_text(encoding="utf-8")


def test_an_undated_document_that_makes_no_state_claim_is_only_documentation():
    reason = dr.outside_gate_reason(REPO_ROOT / "docs/architecture.md")
    assert reason.startswith("documentation"), reason


# ---------------------------------------------------------------------------
# Tags and counts
# ---------------------------------------------------------------------------


@needs_git
def test_every_tag_a_gated_document_names_exists():
    missing = [
        tag
        for tag in dr.named_tags()
        if dr.git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}").returncode != 0
    ]
    assert not missing, f"gated documents name tags that do not exist: {missing}"


@needs_git
def test_a_release_declaration_names_the_commit_its_tag_resolves_to():
    """v0.4.2's page said `7a1ceb9` while the tag resolved to `b00d206`."""
    for tag in ("v0.4.2", "v0.5.0"):
        commit = dr.git("rev-parse", f"refs/tags/{tag}^{{commit}}").stdout.strip()
        text = (REPO_ROOT / "docs" / "releases" / f"{tag}.md").read_text(encoding="utf-8")
        assert commit[:7] in text, f"{tag}'s declaration never names {commit[:7]}"


def test_parse_collection_reads_pytests_summary():
    assert dr.parse_collection("3586/3661 tests collected (75 deselected) in 5.21s") == {
        "collected": 3661,
        "deselected": 75,
    }
    assert dr.parse_collection("42 tests collected in 0.10s") == {
        "collected": 42,
        "deselected": 0,
    }
    assert dr.parse_collection("no summary here") is None


def test_measure_rejects_a_name_it_does_not_know():
    with pytest.raises(KeyError):
        dr.measure("frontend_files")


def test_counts_match_live_measurement():
    """The scale block in `docs/what-msb-v3-is.md` is measured, not remembered."""
    result = dr.Result()
    dr.check_counts(result)
    assert result.findings == [], result.findings
    assert result.stats["declared counts checked"] == sum(
        len(claim.measures) for claim in dr.COUNT_CLAIMS
    )
