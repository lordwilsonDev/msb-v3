"""Dependency ingestion — pyproject.toml, lock files.

Reads pyproject.toml and extracts deps, dev deps, and version pins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from msb_v3.plei.provenance import Provenanced


@dataclass(slots=True)
class DependencyFacts:
    """Dependency-derived facts."""

    runtime_deps: Provenanced = field(default_factory=Provenanced.unknown)
    dev_deps: Provenanced = field(default_factory=Provenanced.unknown)
    python_requirement: Provenanced = field(default_factory=Provenanced.unknown)
    build_system: Provenanced = field(default_factory=Provenanced.unknown)
    scripts: Provenanced = field(default_factory=Provenanced.unknown)


def _read_or_none(path: Path) -> str | None:
    try:
        return path.read_text()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return None


def _parse_toml_deps(content: str) -> tuple[list[str], list[str]]:
    """Extract runtime and dev deps from pyproject.toml using tomllib (3.11+)."""
    import tomllib
    try:
        data = tomllib.loads(content)
    except Exception:
        return [], []
    project = data.get("project", {})
    runtime = [str(d) for d in project.get("dependencies", [])]
    dev = [str(d) for d in project.get("optional-dependencies", {}).get("dev", [])]
    return runtime, dev


def ingest_dependencies(project_root: str | Path) -> DependencyFacts:
    """Ingest dependency information."""
    root = Path(project_root).resolve()
    facts = DependencyFacts()
    source_tag = f"ingestion/dependencies ({root.name})"

    pyproject = _read_or_none(root / "pyproject.toml")
    if not pyproject:
        return facts

    runtime, dev = _parse_toml_deps(pyproject)
    facts.runtime_deps = Provenanced.observed(runtime, f"{root / 'pyproject.toml'} [project.dependencies]")
    facts.dev_deps = Provenanced.observed(dev, f"{root / 'pyproject.toml'} [project.optional-dependencies.dev]")

    import tomllib
    data = tomllib.loads(pyproject)
    project = data.get("project", {})

    python_req = project.get("requires-python", "")
    facts.python_requirement = Provenanced.observed(str(python_req), source_tag)

    build = data.get("build-system", {})
    facts.build_system = Provenanced.observed(
        str(build.get("build-backend", "")),
        source_tag,
    )

    scripts = project.get("scripts", {})
    if isinstance(scripts, dict):
        facts.scripts = Provenanced.observed(list(scripts.keys()), f"{root / 'pyproject.toml'} [project.scripts]")

    return facts


def dep_facts_as_dict(facts: DependencyFacts) -> dict[str, Any]:
    return {k: getattr(facts, k).as_dict() for k in (
        "runtime_deps", "dev_deps", "python_requirement", "build_system", "scripts")}