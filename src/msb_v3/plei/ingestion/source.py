"""Source ingestion — module graph, imports, file inventory.

Walks ``src/`` and collects file counts, top-level packages, and import
dependencies without executing any code.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from msb_v3.plei.provenance import Provenance, Provenanced


@dataclass(slots=True)
class SourceFacts:
    """Source tree facts."""

    file_count: Provenanced = field(default_factory=Provenanced.unknown)
    line_count: Provenanced = field(default_factory=Provenanced.unknown)
    packages: Provenanced = field(default_factory=Provenanced.unknown)  # top-level packages
    core_modules: Provenanced = field(default_factory=Provenanced.unknown)  # key module paths
    import_graph: Provenanced = field(default_factory=Provenanced.unknown)  # package→imports


def _is_python(path: Path) -> bool:
    return path.suffix == ".py" and not path.name.startswith("_")


def _collect_imports(file_path: Path) -> list[str]:
    """Extract import targets from a Python file (no execution)."""
    try:
        tree = ast.parse(file_path.read_text())
    except (SyntaxError, FileNotFoundError):
        return []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split(".")[0])
    return sorted(set(imports))


def ingest_source(project_root: str | Path) -> SourceFacts:
    """Walk the source tree and collect structural facts."""
    root = Path(project_root).resolve()
    facts = SourceFacts()
    source_tag = f"ingestion/source ({root.name})"

    src_dir = root / "src"
    if not src_dir.is_dir():
        facts.file_count = Provenanced(value=0, provenance=Provenance.UNKNOWN, source="src/ not found")
        return facts

    py_files = list(src_dir.rglob("*.py"))
    facts.file_count = Provenanced.observed(len(py_files), source_tag)

    # Line count
    total_lines = 0
    for f in py_files:
        try:
            total_lines += len(f.read_text().split("\n"))
        except Exception:
            pass
    facts.line_count = Provenanced.observed(total_lines, source_tag)

    # Top-level packages (directories under src/ with __init__.py)
    packages = [
        d.name for d in sorted(src_dir.iterdir())
        if d.is_dir() and (d / "__init__.py").exists() and not d.name.startswith("_")
    ]
    facts.packages = Provenanced.observed(packages, source_tag)

    # Core modules — key files (max depth 3 to avoid listing everything)
    core_modules: list[str] = []
    for f in py_files:
        rel = f.relative_to(src_dir)
        parts = rel.parts
        if len(parts) <= 4 and not any(p.startswith("_") for p in parts if p != "__init__.py"):
            core_modules.append(str(rel.with_suffix("")).replace("/", "."))
    facts.core_modules = Provenanced.observed(sorted(core_modules)[:100], source_tag)

    # Import graph — for top-level packages only, what do they import from the project?
    graph: dict[str, list[str]] = {}
    for pkg in packages:
        pkg_dir = src_dir / pkg
        pkg_files = list(pkg_dir.rglob("*.py"))
        all_imports: set[str] = set()
        for f in pkg_files:
            all_imports.update(_collect_imports(f))
        # Only keep project-internal imports
        internal = [i for i in sorted(all_imports) if i in packages or i == "msb_v3"]
        if internal:
            graph[pkg] = internal
    facts.import_graph = Provenanced.inferred(graph, source_tag)

    return facts


def source_facts_as_dict(facts: SourceFacts) -> dict[str, Any]:
    return {k: getattr(facts, k).as_dict() for k in (
        "file_count", "line_count", "packages", "core_modules", "import_graph")}