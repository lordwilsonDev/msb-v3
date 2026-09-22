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
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

PYPROJECT = ROOT / "pyproject.toml"
IDENTITY = SRC / "msb_v3" / "core" / "identity.py"
MANIFEST = ROOT / "MANIFEST.md"
# Dataclass field, 4-space indented: `    version: str = "0.2.1"`.
_IDENTITY_VERSION_RE = re.compile(r'^    version: str = "([^"]+)"', re.MULTILINE)
# The MANIFEST identity table row: | Name / version | `msb-v3` `0.2.3` | ...
_MANIFEST_VERSION_RE = re.compile(r"\| Name / version \| `msb-v3` `([^`]+)` \|")


def _pyproject_version() -> str:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def _identity_version() -> str:
    match = _IDENTITY_VERSION_RE.search(IDENTITY.read_text(encoding="utf-8"))
    assert match is not None, f"no `version: str = \"...\"` field found in {IDENTITY.name}"
    return match.group(1)


def _manifest_version() -> str:
    match = _MANIFEST_VERSION_RE.search(MANIFEST.read_text(encoding="utf-8"))
    assert match is not None, "no `Name / version` row found in MANIFEST.md"
    return match.group(1)


def test_version_sources_agree() -> None:
    # Imported inside the test (after the sys.path insert above) so E402
    # (import-not-at-top) doesn't fire under the repo's ruff selection.
    from msb_v3 import __version__

    pyproject_version = _pyproject_version()
    identity_version = _identity_version()
    manifest_version = _manifest_version()
    assert __version__ == pyproject_version, (
        f"msb_v3.__version__ ({__version__}) != pyproject version ({pyproject_version})"
    )
    assert identity_version == __version__, (
        f"msb_v3.core.identity version ({identity_version}) != "
        f"msb_v3.__version__ ({__version__})"
    )
    assert manifest_version == __version__, (
        f"MANIFEST.md version ({manifest_version}) != "
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
    server), provision that server, and call the verifier with the
    token-authenticated remote. If any of that regresses (workflow renamed,
    trigger dropped, step removed), every release silently skips its
    verification — the dead-wiring failure mode this test exists to catch.

    PRODUCTION-CLOSURE-001 P1 (Option A): server provisioning moved out of the
    workflow YAML and into verify-release.sh, which boots a run-scoped msb-v3
    via scripts/ci-runtime.sh. The wiring check follows it there."""
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
        "verification must run on the sovereign box (macOS + Ollama/Qdrant)"
    )
    steps = job["steps"]
    verifier = next(
        (s for s in steps if "verify-release.sh" in s.get("run", "")), None
    )
    assert verifier is not None, "no step calls verify-release.sh"
    env = verifier.get("env", {})
    assert env.get("VERIFY_REMOTE", "").startswith("https://x-access-token:"), (
        "verifier must clone via the token-authenticated remote (private repo)"
    )
    assert "VERIFY_TAG" in env, "verifier must receive the tag (push ref or dispatch input)"

    # Option A: the server is provisioned by verify-release.sh, not the YAML.
    vr = (ROOT / "scripts" / "verify-release.sh").read_text(encoding="utf-8")
    assert "ci-runtime.sh" in vr and "ci_runtime_start_server" in vr, (
        "verify-release.sh must boot a run-scoped server via scripts/ci-runtime.sh"
    )


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr.strip()}"
    return result.stdout


def test_virgin_checkout_contract_holds_for_head() -> None:
    """verify-release.sh stops before booting anything unless the clone is
    virgin: no tracked `.env`, and NO tracked path under `runtime/` — the check
    is `[ -d "$CLONE_DIR/runtime" ]`, so a single tracked file there is enough.
    Both are machine state, so a clean repo never ships them; tracking one is
    how a checkout stops being reproducible.

    Regression (2026-09-22): commit e3e91fe swept
    `runtime/secret-rotation.log` into the index, so every fresh clone of every
    later commit had a populated runtime/ and `make release-verify` exited 1 at
    this check — before the suite, on every tag cut from v0.4.2 onward. Nothing
    caught it because no test looked at the index; the portability gate can't,
    since it excludes /runtime/ from the staged copy by design.

    Skipped on a staged/foreign checkout (the portability copy has no .git),
    where "tracked" is not a question that can be asked.
    """
    if not (ROOT / ".git").exists():
        pytest.skip("staged / foreign checkout (no .git) — nothing tracked to judge")

    tracked_runtime = _git("ls-files", "runtime").split()
    assert not tracked_runtime, (
        "tracked path(s) under runtime/ redden `make release-verify`'s virginity "
        f"check in every fresh clone: {tracked_runtime}"
    )
    tracked_env = _git("ls-files", ".env").split()
    assert not tracked_env, f"tracked .env would ship secrets to the remote: {tracked_env}"


def test_secret_rotation_log_stays_unversioned() -> None:
    """scripts/rotate_secrets.py appends one line per rotation to
    `runtime/secret-rotation.log` — the key names it rotated and a timestamp,
    never a value — so the path must stay ignored on a host where that script
    runs. This pins the rule, not just today's index: the virginity test above
    would go red again on the next broad `git add`."""
    if not (ROOT / ".git").exists():
        pytest.skip("staged / foreign checkout (no .git) — no ignore rules to read")
    result = subprocess.run(
        ["git", "check-ignore", "-q", "runtime/secret-rotation.log"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "runtime/secret-rotation.log is not gitignored, but scripts/rotate_secrets.py "
        "writes it inside the repo — the next `git add` re-commits machine state"
    )


def test_verify_release_selects_the_tiers_it_documents() -> None:
    """verify-release.sh documents a `-m "not live"` selection — everything but
    the live tier. That is only what actually runs if the tiers are opted into:
    tests/conftest.py DESELECTS integration + chaos from a default collection, so
    a bare `-m "not live"` ran the hermetic core, and the gate never exercised
    the run-scoped server it goes to the trouble of booting (found 2026-09-22).

    Pinned on the suite invocation itself rather than anywhere in the file, so
    it can't be satisfied by an unrelated mention, and asserted against the
    conftest premise below so the two can't silently drift apart.
    """
    script = (ROOT / "scripts" / "verify-release.sh").read_text(encoding="utf-8")
    lines = script.splitlines()
    # "bash scripts/test.sh" — not a bare "scripts/test.sh": the -rfE comment
    # above the invocation mentions the path, and matching that first made this
    # assert against the wrong line (the guard reporting on itself, 2026-09-22).
    index = next((i for i, line in enumerate(lines) if "bash scripts/test.sh" in line), None)
    assert index is not None, "verify-release.sh no longer invokes scripts/test.sh"
    invocation = "\n".join(lines[max(0, index - 3) : index + 1])
    assert "MSB_RUN_TIERS=1" in invocation, (
        "verify-release.sh must set MSB_RUN_TIERS=1 on its suite invocation, or the "
        f"`-m \"not live\"` selection silently runs the hermetic core:\n{invocation}"
    )
    # The premise: conftest only skips that deselection when MSB_RUN_TIERS=1.
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert 'os.environ.get("MSB_RUN_TIERS") == "1"' in conftest, (
        "tests/conftest.py no longer treats MSB_RUN_TIERS=1 as the tier opt-in — "
        "the gate's selection above may now be a no-op"
    )
