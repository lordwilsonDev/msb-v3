"""Acceptance tests for msb_v3.vault.frontmatter — written before the
implementation existed, as the checker for a docs/meta/routing-thesis-assessment.md
killer experiment (see MSB-v3.md's 2026-09-12 checkpoint). The implementation
here is the strong-model-direct comparison arm (single shot, no retries),
kept as the real feature after the experiment concluded."""

from msb_v3.vault.frontmatter import (
    extract_wikilinks,
    find_duplicate_ids,
    parse_frontmatter,
    validate_dates,
    validate_required_fields,
)

# --- parse_frontmatter -------------------------------------------------

def test_parses_scalars_lists_and_quoted_strings():
    text = (
        "---\n"
        'id: "40-handoff-20260912"\n'
        "area: memory\n"
        "tags: [session-handoff, msb-v3, chaos-test]\n"
        "---\n"
        "# Body\nsome content\n"
    )
    fm = parse_frontmatter(text)
    assert fm == {
        "id": "40-handoff-20260912",
        "area": "memory",
        "tags": ["session-handoff", "msb-v3", "chaos-test"],
    }


def test_no_frontmatter_returns_none():
    assert parse_frontmatter("# Just a heading\nno frontmatter here\n") is None


def test_empty_list_value():
    text = "---\ntags: []\n---\nbody\n"
    assert parse_frontmatter(text) == {"tags": []}


def test_unterminated_block_returns_none():
    text = "---\nid: x\nno closing delimiter\n"
    assert parse_frontmatter(text) is None


# --- validate_required_fields ------------------------------------------

def test_all_present_is_clean():
    fm = {"id": "1", "area": "memory", "type": "note"}
    assert validate_required_fields(fm, ["id", "area", "type"]) == []


def test_reports_missing_in_given_order():
    fm = {"id": "1"}
    assert validate_required_fields(fm, ["id", "area", "type"]) == ["area", "type"]


def test_none_frontmatter_means_all_missing():
    assert validate_required_fields(None, ["id", "area"]) == ["id", "area"]


# --- validate_dates ------------------------------------------------------

def test_valid_dates_in_order_is_clean():
    fm = {"created": "2026-08-16", "updated": "2026-09-12"}
    assert validate_dates(fm) == []


def test_no_date_fields_is_clean():
    assert validate_dates({"id": "1"}) == []


def test_invalid_format_is_flagged():
    violations = validate_dates({"created": "08/16/2026"})
    assert len(violations) == 1
    assert "created" in violations[0]


def test_updated_before_created_is_flagged():
    violations = validate_dates({"created": "2026-09-12", "updated": "2026-08-16"})
    assert len(violations) == 1
    assert "2026-09-12" in violations[0] and "2026-08-16" in violations[0]


def test_only_created_present_is_clean():
    assert validate_dates({"created": "2026-08-16"}) == []


# --- extract_wikilinks --------------------------------------------------

def test_extracts_simple_links_in_order():
    text = "See [[Automation-Factory]] and then [[MSB-v3]] for details."
    assert extract_wikilinks(text) == ["Automation-Factory", "MSB-v3"]


def test_dedupes_repeated_links():
    text = "[[MSB-v3]] is mentioned twice: [[MSB-v3]] again."
    assert extract_wikilinks(text) == ["MSB-v3"]


def test_alias_syntax_returns_target_not_alias():
    text = "[[Automation-Factory|the automation project]]"
    assert extract_wikilinks(text) == ["Automation-Factory"]


def test_no_links_is_empty():
    assert extract_wikilinks("plain text, no links") == []


# --- find_duplicate_ids -------------------------------------------------

def test_finds_shared_ids():
    notes = {
        "a.md": {"id": "10-020"},
        "b.md": {"id": "10-020"},
        "c.md": {"id": "10-021"},
    }
    assert find_duplicate_ids(notes) == {"10-020": ["a.md", "b.md"]}


def test_all_unique_is_empty():
    notes = {"a.md": {"id": "1"}, "b.md": {"id": "2"}}
    assert find_duplicate_ids(notes) == {}


def test_ignores_missing_frontmatter_and_missing_id():
    notes = {
        "a.md": None,
        "b.md": {"area": "memory"},
        "c.md": {"id": "1"},
    }
    assert find_duplicate_ids(notes) == {}
