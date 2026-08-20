"""Close-out AC-3.1: the surface-area map covers every router + subpackage.

docs/SURFACE.md classifies every api/ router and src/msb_v3/ subpackage as
LOAD-BEARING / OPTIONAL / FROZEN. This test makes the map self-enforcing: a
router or package that exists on disk but is missing from the map (or appears
without a classification) fails the suite, so the map cannot silently drift
from the tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SURFACE = ROOT / "docs" / "SURFACE.md"
CLASSES = ("LOAD-BEARING", "OPTIONAL", "FROZEN")

pytestmark = pytest.mark.skipif(not SURFACE.exists(), reason="SURFACE.md missing")


def _doc_lines() -> list[str]:
    return SURFACE.read_text(encoding="utf-8").splitlines()


def _classified_line(token: str, lines: list[str]) -> str | None:
    """The table line mentioning `token` that carries a classification."""
    for line in lines:
        if token in line and any(c in line for c in CLASSES):
            return line
    return None


def test_surface_map_covers_every_api_router() -> None:
    lines = _doc_lines()
    routers = sorted(p.stem for p in (ROOT / "src" / "msb_v3" / "api").glob("*.py"))
    assert routers, "no api routers found — tree changed?"
    missing: list[str] = []
    unclassified: list[str] = []
    for name in routers:
        token = f"api/{name}.py"
        if not any(token in line for line in lines):
            missing.append(name)
            continue
        if _classified_line(token, lines) is None:
            unclassified.append(name)
    assert not missing, f"routers missing from docs/SURFACE.md: {missing}"
    assert not unclassified, f"routers in SURFACE.md without a classification: {unclassified}"


def test_surface_map_covers_node_and_vesta_routers() -> None:
    """The node and vesta routers live in their subpackages, not api/."""
    lines = _doc_lines()
    for module in ("msb_v3/node/api.py", "msb_v3/vesta/api.py"):
        assert any(module in line for line in lines), f"{module} missing from docs/SURFACE.md"
        assert _classified_line(module, lines) is not None, f"{module} unclassified"


def test_surface_map_covers_every_subpackage() -> None:
    lines = _doc_lines()
    src = ROOT / "src" / "msb_v3"
    packages = sorted(
        p.name
        for p in src.iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "__init__.py").exists()
    )
    assert packages, "no subpackages found — tree changed?"
    missing: list[str] = []
    unclassified: list[str] = []
    for name in packages:
        token = f"msb_v3/{name}"
        if not any(token in line for line in lines):
            missing.append(name)
            continue
        if _classified_line(token, lines) is None:
            unclassified.append(name)
    assert not missing, f"subpackages missing from docs/SURFACE.md: {missing}"
    assert not unclassified, f"subpackages in SURFACE.md without a classification: {unclassified}"


def test_surface_map_covers_top_level_packages() -> None:
    lines = _doc_lines()
    for package in ("msb_ledger", "personal_intelligence"):
        assert _classified_line(f"`{package}`", lines) is not None, f"{package} unclassified in SURFACE.md"
