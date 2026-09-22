"""Close-out AC-3.1: the surface-area map covers every router + subpackage.

docs/SURFACE.md classifies every api/ router and src/msb_v3/ subpackage as
LOAD-BEARING / OPTIONAL / FROZEN. This test makes the map self-enforcing: a
router or package that exists on disk but is missing from the map (or appears
without a classification) fails the suite, so the map cannot silently drift
from the tree.

Three properties, each of which used to be a hole in this gate:

1. **Namespace packages count.** Membership is "a non-private directory holding
   at least one module", not "has an ``__init__.py``". ``core``, ``api``,
   ``agent``, ``db``, ``memory``, ``integrations`` and friends ship no
   ``__init__.py``; the old filter made all of them invisible, and
   ``src/msb_v3/integrations/`` — a mounted, tested, operator-gated subsystem —
   went unclassified without this gate ever noticing.
2. **Rows match exactly.** A row is bound to its own token, so a shorter
   package can never be satisfied by a longer one's row (``msb_v3/memory``
   shadowed by ``msb_v3/memory_fabric``; ``msb_v3/node`` by
   ``msb_v3/node/api.py``).
3. **The direction is two-way.** A row whose target no longer exists on disk
   fails too, so the map cannot keep classifying a subsystem that was deleted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SURFACE = ROOT / "docs" / "SURFACE.md"
CLASSES = ("LOAD-BEARING", "OPTIONAL", "FROZEN")

pytestmark = pytest.mark.skipif(not SURFACE.exists(), reason="SURFACE.md missing")


def _doc_lines() -> list[str]:
    return SURFACE.read_text(encoding="utf-8").splitlines()


def _declared_token(line: str) -> str | None:
    """The token a table row declares: the first backticked cell.

    Reading the first cell — rather than searching the whole line — is what
    kills substring shadowing: a row can only ever declare its own name, so
    `msb_v3/node` is never satisfied by the `msb_v3/node/api.py` row. An
    annotation inside the cell (``... `msb_v3/node/api.py` (router)``) is fine.
    """
    match = re.match(r"^\|\s*([^|]+)\|\s*([^|]+)\|?", line)
    if not match:
        return None
    found = re.findall(r"`([^`]+)`", match.group(1))
    return found[0].strip() if found else None


def _row(token: str, lines: list[str]) -> str | None:
    """The table row that declares exactly `token`."""
    for line in lines:
        if _declared_token(line) == token:
            return line
    return None


def _classified_row(token: str, lines: list[str]) -> str | None:
    """The declaration row for `token`, only if it carries a classification."""
    row = _row(token, lines)
    if row is not None and any(c in row for c in CLASSES):
        return row
    return None


def _declared_tokens(prefix: str, lines: list[str], *, bare_only: bool = False) -> set[str]:
    """Every `prefix/<name>` this map declares — the reverse direction.

    `bare_only` keeps just the top-level names (no nested module paths such as
    `node/api.py`), which is what the subpackage table is compared against.
    """
    found: set[str] = set()
    for line in lines:
        token = _declared_token(line)
        if token is None or not token.startswith(f"{prefix}/"):
            continue
        name = token[len(prefix) + 1 :]
        if bare_only and ("/" in name or "." in name):
            continue
        found.add(name)
    return found


def _routers() -> list[str]:
    return sorted(p.name for p in (ROOT / "src" / "msb_v3" / "api").glob("*.py"))


def _subpackages() -> list[str]:
    """Every real subpackage directory — namespace packages included."""
    src = ROOT / "src" / "msb_v3"
    return sorted(
        p.name
        for p in src.iterdir()
        if p.is_dir()
        and not p.name.startswith("_")
        and p.name != "__pycache__"
        and next(p.glob("*.py"), None) is not None
    )


def _assert_covered(kind: str, tokens: list[str], lines: list[str]) -> None:
    missing = [t for t in tokens if _row(t, lines) is None]
    assert not missing, f"{kind} missing from docs/SURFACE.md: {missing}"
    unclassified = [t for t in tokens if _classified_row(t, lines) is None]
    assert not unclassified, f"{kind} in SURFACE.md without a classification: {unclassified}"


def test_surface_map_covers_every_api_router() -> None:
    lines = _doc_lines()
    routers = _routers()
    assert routers, "no api routers found — tree changed?"

    _assert_covered("routers", [f"api/{name}" for name in routers], lines)

    ghosts = sorted(_declared_tokens("api", lines) - set(routers))
    assert not ghosts, f"SURFACE.md classifies routers that no longer exist: {ghosts}"


def test_surface_map_covers_node_and_vesta_routers() -> None:
    """The node and vesta routers live in their subpackages, not api/."""
    lines = _doc_lines()
    for module in ("msb_v3/node/api.py", "msb_v3/vesta/api.py"):
        _assert_covered("nestled router", [module], lines)


def test_surface_map_covers_every_subpackage() -> None:
    lines = _doc_lines()
    packages = _subpackages()
    assert packages, "no subpackages found — tree changed?"

    _assert_covered("subpackages", [f"msb_v3/{name}" for name in packages], lines)

    declared = _declared_tokens("msb_v3", lines, bare_only=True)
    ghosts = sorted(declared - set(packages))
    assert not ghosts, f"SURFACE.md classifies subpackages that no longer exist: {ghosts}"


def test_surface_map_covers_top_level_packages() -> None:
    lines = _doc_lines()
    for package in ("msb_ledger", "personal_intelligence"):
        assert (ROOT / "src" / package).is_dir(), f"{package} missing from disk — tree changed?"
        assert _classified_row(package, lines) is not None, f"{package} unclassified in SURFACE.md"
