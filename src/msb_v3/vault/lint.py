"""Vault-wide lint: walk every note, aggregate frontmatter and cross-link issues.

Read-only by construction — a report only, never a write. ``required_fields``
is caller-supplied rather than a hardcoded schema: the vault's per-type
frontmatter conventions (see CLAUDE.md / LM-WIKI-SCHEMA.md) evolve, and this
module has no authority to encode a specific one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from msb_v3.vault.frontmatter import (
    extract_wikilinks,
    find_duplicate_ids,
    parse_frontmatter,
    validate_dates,
    validate_required_fields,
)


@dataclass
class VaultLintReport:
    notes_scanned: int = 0
    missing_fields: dict = field(default_factory=dict)  # relpath -> [field, ...]
    date_violations: dict = field(default_factory=dict)  # relpath -> [violation, ...]
    duplicate_ids: dict = field(default_factory=dict)  # id -> [relpath, ...]
    dangling_links: dict = field(default_factory=dict)  # relpath -> [target, ...]

    @property
    def clean(self) -> bool:
        return not (self.missing_fields or self.date_violations or self.duplicate_ids or self.dangling_links)


def lint_vault(vault_root: str | Path, *, required_fields: list[str] | None = None) -> VaultLintReport:
    """Scan every ``*.md`` file under ``vault_root``. ``required_fields`` (if
    given) is checked against every note uniformly — this does not attempt
    per-note-type schemas."""
    root = Path(vault_root).expanduser().resolve()
    md_files = sorted(root.rglob("*.md"))
    # A wikilink can target any real vault file, not just notes — Obsidian
    # links/embeds .csv, .yaml, .pdf, images, etc. Found linting the real
    # vault 2026-09-12: a .csv prospect registry and a .yaml decision-card
    # template were both flagged as dangling because known_names only
    # indexed *.md files.
    linkable_files = [
        p for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts and ".obsidian" not in p.parts
    ]
    # A wikilink target may be a bare stem ([[Note]]), a path relative to the
    # vault root ([[folder/Note]] / [[folder/Note.md]]), or — Obsidian's
    # "shortest unique path" convention, the LM-Wiki-Schema raw/wiki layout
    # relies on it — any trailing suffix of the real path ([[raw/Note]] for
    # a file at .../TopicWiki/raw/Note.md). Found linting the real vault
    # 2026-09-12: several LM-Wiki pages link this way and a
    # root-relative-or-bare-stem-only set incorrectly flagged them as
    # dangling even though the note exists. Every suffix is added (not just
    # the shortest), since a link may name as many trailing segments as the
    # author chose for disambiguation.
    known_names: set = set()
    for p in linkable_files:
        parts = p.relative_to(root).parts
        ext = p.suffix  # "" for extensionless files
        for i in range(len(parts)):
            suffix = "/".join(parts[i:])
            suffix_no_ext = suffix[: -len(ext)] if ext else suffix
            known_names.add(suffix_no_ext)
            known_names.add(suffix)

    notes: dict = {}
    bodies: dict = {}
    for path in md_files:
        rel = str(path.relative_to(root))
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        notes[rel] = parse_frontmatter(text)
        bodies[rel] = text

    report = VaultLintReport(notes_scanned=len(notes))

    if required_fields:
        for rel, frontmatter in notes.items():
            missing = validate_required_fields(frontmatter, required_fields)
            if missing:
                report.missing_fields[rel] = missing

    for rel, frontmatter in notes.items():
        if not frontmatter:
            continue
        violations = validate_dates(frontmatter)
        if violations:
            report.date_violations[rel] = violations

    report.duplicate_ids = find_duplicate_ids(notes)

    for rel, text in bodies.items():
        dangling = []
        for target in extract_wikilinks(text):
            # [[Note#Heading]] links to a heading inside another note — the
            # note itself is what must exist, not the literal "Note#Heading"
            # string. (extract_wikilinks already filters out a *same-file*
            # "#Heading" target on its own, handled separately from this.)
            note_part = target.split("#", 1)[0]
            if note_part not in known_names:
                dangling.append(target)
        if dangling:
            report.dangling_links[rel] = dangling

    return report
