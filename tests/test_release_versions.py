"""Release-checklist guard — the version sources must agree.

The release version lives in three places: `pyproject.toml` (`project.version`),
`msb_v3.__version__` (single source of truth for /system/info, /system/config,
/mcp/status), and the `sovereign_runtime` identity default. The 2026-08-13
v0.2.1 cut proved drift is real: `identity.py` stayed at 0.2.0 while the
release bumped the other two, and only the portability gate's
`test_identity_deterministic` caught it (it asserts identity.version ==
__version__ but nothing tied either to pyproject). This test makes the
three-way agreement explicit so version drift fails the suite at the source,
not only when a release is pushed.

Deliberately NOT asserted here: the git tag — the version bump commit
legitimately precedes the tag by definition, so tag-matching would break
every pre-release commit.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

PYPROJECT = ROOT / "pyproject.toml"
IDENTITY = SRC / "sovereign_runtime" / "core" / "identity.py"
# Dataclass field, 4-space indented: `    version: str = "0.2.1"`.
_IDENTITY_VERSION_RE = re.compile(r'^    version: str = "([^"]+)"', re.MULTILINE)


def _pyproject_version() -> str:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def _identity_version() -> str:
    match = _IDENTITY_VERSION_RE.search(IDENTITY.read_text(encoding="utf-8"))
    assert match is not None, f"no `version: str = \"...\"` field found in {IDENTITY.name}"
    return match.group(1)


def test_version_sources_agree() -> None:
    # Imported inside the test (after the sys.path insert above) so E402
    # (import-not-at-top) doesn't fire under the repo's ruff selection.
    from msb_v3 import __version__

    pyproject_version = _pyproject_version()
    identity_version = _identity_version()
    assert __version__ == pyproject_version, (
        f"msb_v3.__version__ ({__version__}) != pyproject version ({pyproject_version})"
    )
    assert identity_version == __version__, (
        f"sovereign_runtime identity version ({identity_version}) != "
        f"msb_v3.__version__ ({__version__})"
    )


def test_release_verify_script_wired() -> None:
    """The release-verification script must exist and be reachable via the
    Makefile — the v0.2.3 flow (fresh-clone + seed + full suite from a virgin
    checkout) is the only thing that proves a tag as others fetch it. If the
    script or its entry point ever disappears, this fails at the source."""
    script = ROOT / "scripts" / "verify-release.sh"
    assert script.is_file(), f"verify-release.sh missing: {script}"
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "verify-release:" in makefile, "Makefile has no verify-release target"
    assert "verify-release.sh" in makefile, "verify-release target does not call the script"
