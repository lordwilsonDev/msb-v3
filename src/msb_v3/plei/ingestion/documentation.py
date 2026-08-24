"""Documentation ingestion — README, CHANGELOG, MANIFEST, docs/.

Reads top-level docs and extracts structured facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from msb_v3.plei.provenance import Provenanced

_DOC_FILES = {
    "readme": "README.md",
    "changelog": "CHANGELOG.md",
    "manifest": "MANIFEST.md",
    "contributing": "CONTRIBUTING.md",
    "license": "LICENSE",
    "claude": "CLAUDE.md",
    "project_map": "docs/project-map.md",
    "architecture": "docs/SURFACE.md",
    "prerequisites": "docs/PREREQUISITES.md",
    "runbook": "docs/ops-runbook.md",
}


def _read_or_none(path: Path) -> str | None:
    try:
        return path.read_text()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return None


def _extract_section(content: str, heading: str) -> str | None:
    """Extract text under a markdown heading until the next heading of same or higher level."""
    if not content:
        return None
    lines = content.split("\n")
    in_section = False
    collected: list[str] = []
    for line in lines:
        if line.startswith("# ") and heading.lower() in line.lower():
            in_section = True
            continue
        if in_section:
            if line.startswith("# "):
                break
            collected.append(line)
    return "\n".join(collected).strip() if collected else None


@dataclass(slots=True)
class DocumentationFacts:
    """Structured facts extracted from project documentation."""

    presence: Provenanced = field(default_factory=Provenanced.unknown)  # which docs exist
    readme_summary: Provenanced = field(default_factory=Provenanced.unknown)
    version: Provenanced = field(default_factory=Provenanced.unknown)
    mission: Provenanced = field(default_factory=Provenanced.unknown)
    architecture_summary: Provenanced = field(default_factory=Provenanced.unknown)
    endpoints: Provenanced = field(default_factory=Provenanced.unknown)
    env_vars: Provenanced = field(default_factory=Provenanced.unknown)
    supervision: Provenanced = field(default_factory=Provenanced.unknown)
    gaps: Provenanced = field(default_factory=Provenanced.unknown)
    debt: Provenanced = field(default_factory=Provenanced.unknown)
    project_map_sections: Provenanced = field(default_factory=Provenanced.unknown)


def ingest_documentation(project_root: str | Path) -> DocumentationFacts:
    """Ingest all discoverable documentation from the project tree."""
    root = Path(project_root).resolve()
    facts = DocumentationFacts()
    source_tag = f"ingestion/documentation ({root.name})"

    # Which docs are present
    present: dict[str, bool] = {}
    for name, rel in _DOC_FILES.items():
        present[name] = (root / rel).is_file()
    facts.presence = Provenanced.observed(present, source_tag)

    # README
    readme = _read_or_none(root / "README.md")
    if readme:
        facts.readme_summary = Provenanced.observed(
            readme[:500], f"{root / 'README.md'}"
        )
        mission_text = _extract_section(readme, "What it is")
        facts.mission = Provenanced.observed(mission_text or readme[:200], f"{root / 'README.md'} #what-it-is")

    # CHANGELOG for version
    changelog = _read_or_none(root / "CHANGELOG.md")
    if changelog:
        # Extract latest version header
        for line in changelog.split("\n"):
            if line.startswith("## [") and "]" in line:
                facts.version = Provenanced.observed(
                    line.strip("## [] "), f"{root / 'CHANGELOG.md'}"
                )
                break

    # MANIFEST
    manifest = _read_or_none(root / "MANIFEST.md")
    if manifest:
        facts.architecture_summary = Provenanced.observed(
            manifest[:400], f"{root / 'MANIFEST.md'}"
        )
        # Extract gaps section
        gaps = _extract_section(manifest, "Gaps")
        if gaps:
            facts.gaps = Provenanced.observed(gaps[:1000], f"{root / 'MANIFEST.md'}")

    # Env vars from .env.example
    env_example = _read_or_none(root / ".env.example")
    if env_example:
        env_lines = [line.strip() for line in env_example.split("\n") if line.strip() and not line.strip().startswith("#")]
        facts.env_vars = Provenanced.observed(env_lines, f"{root / '.env.example'}")

    # Runbook for supervision
    runbook = _read_or_none(root / "docs" / "ops-runbook.md")
    if runbook:
        facts.supervision = Provenanced.observed(
            runbook[:600], f"{root / 'docs/ops-runbook.md'}"
        )

    # Project map for debt and structured data
    project_map = _read_or_none(root / "docs" / "project-map.md")
    if project_map:
        sections: dict[str, str] = {}
        current_section = "preamble"
        sections[current_section] = ""
        for line in project_map.split("\n"):
            if line.startswith("## ") and not line.startswith("### "):
                current_section = line.strip("## ").strip()
                sections[current_section] = ""
            else:
                sections[current_section] = sections.get(current_section, "") + "\n" + line
        facts.project_map_sections = Provenanced.observed(
            {k: v[:300] for k, v in sections.items() if v.strip()},
            f"{root / 'docs/project-map.md'}"
        )
        # Debt
        debt_section = _extract_section(project_map, "Technical debt")
        if debt_section:
            facts.debt = Provenanced.observed(debt_section[:1000], f"{root / 'docs/project-map.md'} #technical-debt")

    return facts


def doc_facts_as_dict(facts: DocumentationFacts) -> dict[str, Any]:
    return {k: getattr(facts, k).as_dict()
            for k in ("presence", "readme_summary", "version", "mission",
                       "architecture_summary", "endpoints", "env_vars",
                       "supervision", "gaps", "debt", "project_map_sections")}