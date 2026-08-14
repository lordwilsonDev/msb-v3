"""Release-checklist guard — the version sources must agree.

The release version lives in three places: `pyproject.toml` (`project.version`),
`msb_v3.__version__` (single source of truth for /system/info, /system/config,
/mcp/status), and the `msb_v3.core.identity` default. The 2026-08-13
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

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

PYPROJECT = ROOT / "pyproject.toml"
IDENTITY = SRC / "msb_v3" / "core" / "identity.py"
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
        f"msb_v3.core.identity version ({identity_version}) != "
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
    # The target BLOCK (not just the filename anywhere) must invoke the script
    # — `verify-release: echo verify-release.sh` would otherwise pass.
    target = re.search(r"^verify-release:\s*\n((?:\t[^\n]*\n?)+)", makefile, re.MULTILINE)
    assert target is not None, "Makefile has no verify-release target"
    assert "verify-release.sh" in target.group(1), (
        "verify-release target does not call the script"
    )


def test_release_verify_ci_workflow_wired() -> None:
    """The auto-verification workflow must exist, trigger on tag pushes, run
    on the self-hosted runner (the suite's live tests need the :8766 dev
    server), ensure that server, and call the verifier with the
    token-authenticated remote. If any of that regresses (workflow renamed,
    trigger dropped, step removed), every release silently skips its
    verification — the dead-wiring failure mode this test exists to catch."""
    wf = ROOT / ".github" / "workflows" / "release-verify.yml"
    assert wf.is_file(), f"release-verify.yml missing: {wf}"
    data = yaml.safe_load(wf.read_text(encoding="utf-8"))
    # PyYAML (YAML 1.1) parses the bare `on:` key as boolean True; GitHub
    # uses YAML 1.2 where it stays a string. Accept both so the test runs
    # under either loader.
    triggers = data.get("on") or data.get(True) or {}
    assert triggers, "workflow has no trigger section"
    assert "push" in triggers, "workflow must trigger on tag pushes"
    assert triggers["push"]["tags"], "workflow must filter to tag pushes"
    assert "workflow_dispatch" in triggers, "workflow must be dispatchable for manual verification"
    job = data["jobs"]["verify"]
    assert job["runs-on"] == ["self-hosted", "macOS"], (
        "verification must run on the sovereign box (live :8766 dev server)"
    )
    steps = job["steps"]
    assert any("make server-start" in s.get("run", "") for s in steps), (
        "workflow must ensure the :8766 dev server (suite live tests)"
    )
    verifier = next(
        (s for s in steps if "verify-release.sh" in s.get("run", "")), None
    )
    assert verifier is not None, "no step calls verify-release.sh"
    env = verifier.get("env", {})
    assert env.get("VERIFY_REMOTE", "").startswith("https://x-access-token:"), (
        "verifier must clone via the token-authenticated remote (private repo)"
    )
    assert "VERIFY_TAG" in env, "verifier must receive the tag (push ref or dispatch input)"
