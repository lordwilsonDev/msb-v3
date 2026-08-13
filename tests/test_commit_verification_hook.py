"""Gate the commit-verification hook's fail-loud invariant.

The hook at ~/.agents/hooks/require-verified-claims.sh must NEVER silently
fail open on an unresolvable cwd. That silent fail-open is exactly what
let the 2026-08-08 fabricated-commit incident class go unguarded via
Hermes (the gateway process cwd was sent in place of the session's real
working directory, so `git -C "$CWD"` always failed and the hook allowed
every commit, quietly). The hook now blocks loudly on that case and
ships a `--selftest`; this test runs it on any machine where the hook is
installed. Skipped on CI runners that don't carry the hook — the guard
is for the machines that actually run the hook.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path.home() / ".agents" / "hooks" / "require-verified-claims.sh"

pytestmark = pytest.mark.skipif(
    not HOOK.is_file()
    or shutil.which("jq") is None
    or shutil.which("git") is None,
    reason="commit-verification hook (or jq/git) not installed on this machine",
)


def test_hook_selftest_passes() -> None:
    """The hook's own self-check must pass: unresolvable cwd blocks loudly,
    missing cwd warns-and-allows, non-commit commands always allow."""
    result = subprocess.run(
        ["bash", str(HOOK), "--selftest"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"hook selftest failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PASS" in result.stdout
