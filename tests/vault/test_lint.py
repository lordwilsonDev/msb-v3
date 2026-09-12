from msb_v3.vault.lint import lint_vault


def _write(root, relpath, content):
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_clean_vault_reports_clean(tmp_path):
    _write(tmp_path, "A.md", "---\nid: 1\ncreated: 2026-01-01\n---\nbody\n")
    report = lint_vault(tmp_path, required_fields=["id"])
    assert report.notes_scanned == 1
    assert report.clean


def test_missing_required_fields(tmp_path):
    _write(tmp_path, "A.md", "---\nid: 1\n---\nbody\n")
    report = lint_vault(tmp_path, required_fields=["id", "area"])
    assert report.missing_fields == {"A.md": ["area"]}


def test_no_required_fields_means_no_check(tmp_path):
    _write(tmp_path, "A.md", "---\nid: 1\n---\nbody\n")
    report = lint_vault(tmp_path)
    assert report.missing_fields == {}


def test_date_violations_surface(tmp_path):
    _write(tmp_path, "A.md", "---\ncreated: 2026-09-12\nupdated: 2026-01-01\n---\nbody\n")
    report = lint_vault(tmp_path)
    assert "A.md" in report.date_violations


def test_duplicate_ids_across_files(tmp_path):
    _write(tmp_path, "A.md", "---\nid: dup\n---\nbody\n")
    _write(tmp_path, "sub/B.md", "---\nid: dup\n---\nbody\n")
    report = lint_vault(tmp_path)
    assert report.duplicate_ids == {"dup": ["A.md", "sub/B.md"]}


def test_dangling_link_detected(tmp_path):
    _write(tmp_path, "A.md", "---\nid: 1\n---\nSee [[Nonexistent-Note]] for more.\n")
    report = lint_vault(tmp_path)
    assert report.dangling_links == {"A.md": ["Nonexistent-Note"]}


def test_path_style_link_to_a_real_note_is_not_dangling(tmp_path):
    """Regression: found linting the real vault 2026-09-12 — several SOPs
    link by full path ([[folder/_INDEX]]) rather than bare stem, and a
    stem-only known-names set incorrectly flagged the note as missing."""
    _write(tmp_path, "sub/_INDEX.md", "---\nid: 1\n---\nbody\n")
    _write(tmp_path, "A.md", "---\nid: 2\n---\nSee [[sub/_INDEX]] for the index.\n")
    report = lint_vault(tmp_path)
    assert report.dangling_links == {}


def test_real_link_is_not_dangling(tmp_path):
    _write(tmp_path, "A.md", "---\nid: 1\n---\nSee [[B]] for more.\n")
    _write(tmp_path, "B.md", "---\nid: 2\n---\nbody\n")
    report = lint_vault(tmp_path)
    assert report.dangling_links == {}


def test_unparseable_note_is_skipped_not_crashed(tmp_path):
    _write(tmp_path, "A.md", "no frontmatter at all\n")
    report = lint_vault(tmp_path, required_fields=["id"])
    assert report.notes_scanned == 1
    # frontmatter=None -> "id" counts as missing, not a crash
    assert report.missing_fields == {"A.md": ["id"]}
