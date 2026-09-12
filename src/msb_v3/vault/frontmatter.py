"""Frontmatter parsing and per-note validation. Standard library only.

Parses the ``---``-delimited YAML-ish frontmatter block Obsidian notes use
(see CLAUDE.md's "Obsidian vault logging" conventions) without pulling in a
full YAML parser — the vault's frontmatter is flat key/value plus simple
comma lists, not nested structures.
"""

from __future__ import annotations


def parse_frontmatter(text: str) -> dict | None:
    """Extract the frontmatter block from a note's raw text.

    Fails closed to ``None`` (not a partial dict) on anything malformed: no
    leading ``---`` line, or no matching closing ``---`` line — a truncated
    parse is worse than an honest "couldn't find frontmatter."
    """
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        return None

    end = None
    for i in range(1, len(lines)):
        if lines[i] == "---":
            end = i
            break
    if end is None:
        return None

    result: dict = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        if line[:1] in (" ", "\t"):
            # Indented -> belongs to a nested block under the previous
            # top-level key (e.g. a `metadata:` sub-block). Not flattened:
            # a nested `metadata.updated` is a different field from a
            # top-level `updated`, and conflating them produces false
            # positives (found 2026-09-12 linting the real vault: several
            # notes' nested `metadata.created/updated` — describing when
            # the underlying fact was true — got misread as the note's own
            # created/updated, which come from the top-level fields only).
            continue
        if ":" not in line:
            continue
        key, _, raw_value = line.partition(":")
        key = key.strip()
        value = raw_value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            result[key] = [] if not inner else [_strip_quotes(item.strip()) for item in inner.split(",")]
        else:
            result[key] = _strip_quotes(value)
    return result


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def validate_required_fields(frontmatter: dict | None, required: list[str]) -> list[str]:
    """Names from ``required`` missing from ``frontmatter``, in the given order.
    ``frontmatter=None`` (parse failure) means every required name is missing."""
    if frontmatter is None:
        return list(required)
    return [name for name in required if name not in frontmatter]


def validate_dates(frontmatter: dict) -> list[str]:
    """Check ``created``/``updated`` are ``YYYY-MM-DD`` and ``updated`` isn't
    before ``created``. Only looks at fields that are actually present."""
    import re

    violations = []
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    created = frontmatter.get("created")
    updated = frontmatter.get("updated")

    if created is not None and not date_re.match(created):
        violations.append(f"invalid date format for 'created': {created}")
    if updated is not None and not date_re.match(updated):
        violations.append(f"invalid date format for 'updated': {updated}")

    if (
        created is not None and updated is not None
        and date_re.match(created) and date_re.match(updated)
        and updated < created
    ):
        violations.append(f"'updated' ({updated}) is before 'created' ({created})")

    return violations


def extract_wikilinks(text: str) -> list[str]:
    """Unique ``[[target]]``/``[[target|alias]]`` targets, in first-appearance order.

    Excludes purely numeric/comma bracket contents (``[[196,197]]``,
    ``[[316]]``) — found linting the real vault 2026-09-12: some notes use
    ``[[...]]`` for citation/page-range annotations unrelated to Obsidian
    wikilinks, and a target with no letters can never be a real note name.
    """
    import re

    seen: list[str] = []
    for match in re.finditer(r"\[\[([^\]]+)\]\]", text):
        target = match.group(1).split("|", 1)[0]
        if not re.search(r"[a-zA-Z]", target):
            continue
        if target not in seen:
            seen.append(target)
    return seen


def find_duplicate_ids(notes: dict) -> dict:
    """``notes`` maps path -> frontmatter (or None). Returns id -> [paths] only
    for ids shared by more than one note; notes without an id are ignored."""
    by_id: dict = {}
    for path, frontmatter in notes.items():
        if not frontmatter or "id" not in frontmatter:
            continue
        by_id.setdefault(frontmatter["id"], []).append(path)
    return {id_: paths for id_, paths in by_id.items() if len(paths) > 1}
