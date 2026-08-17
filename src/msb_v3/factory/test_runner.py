"""Software Factory test runner (spec §4.2.6 — test stage).

Detects the repo's test command from its own tooling and runs it in the
worktree, capturing real evidence: command, exit code, output, duration.
No command found -> ``ran=False`` (the verifier treats missing evidence as
UNVERIFIED, never as a pass).
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Optional

from msb_v3.factory.models import TestEvidence


def detect_test_command(worktree: str) -> Optional[str]:
    import sys

    root = Path(worktree)
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists():
        # The interpreter running the factory is the one guaranteed to have
        # its dependencies (pytest) — sys.executable, not whatever `python`
        # happens to resolve to on PATH.
        py = sys.executable or shutil.which("python") or "python3"
        return f"{py} -m pytest -q"
    if (root / "Makefile").exists():
        return "make test"
    if (root / "package.json").exists():
        return "npm test"
    if (root / "go.mod").exists():
        return "go test ./..."
    return None


# A change is docs-only when every changed file is a documentation artifact:
# a text-doc extension, a known doc filename, or under a docs directory.
# Such a change cannot break code, so the full suite is not required — the
# pipeline records a classified skip instead of running (and timing out on)
# the whole test suite. This is what lets a docs change reach MERGED.
_DOC_EXTENSIONS = (".md", ".markdown", ".rst", ".txt", ".adoc", ".asciidoc", ".tex")
_DOC_FILENAMES = {"readme", "readme.md", "license", "license.txt", "changelog", "contributing", "authors"}
_DOC_DIRS = ("docs", "documentation", "doc")


def is_docs_only_change(changed_files: list[str] | tuple[str, ...]) -> bool:
    """True when every changed file is documentation (or there are none)."""
    if not changed_files:
        return True  # no change at all — callers treat this as build-noop
    for rel in changed_files:
        p = rel.replace("\\", "/").lower()
        if p.endswith(_DOC_EXTENSIONS):
            continue
        if p.rstrip("/").split("/")[-1] in _DOC_FILENAMES:
            continue
        parts = p.split("/")
        if any(part in _DOC_DIRS for part in parts[:-1]):
            continue
        return False
    return True


def docs_only_skip() -> TestEvidence:
    """The classified-skip evidence for a docs-only change: recorded in the
    chain with the reason, and treated as PASS by the verifier (distinct
    from ran=False, which stays honest UNVERIFIED)."""
    return TestEvidence(
        skipped=True,
        skip_reason="docs-only change — full test suite not required by policy",
    )


async def run_tests(worktree: str, *, command: Optional[str] = None, timeout_s: float = 300.0) -> TestEvidence:
    cmd = command or detect_test_command(worktree)
    if not cmd:
        return TestEvidence(command="", ran=False)
    started = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd.split(),
            cwd=worktree,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        duration = round(time.perf_counter() - started, 3)
        text = (out_bytes or b"").decode("utf-8", errors="replace")
        return TestEvidence(
            command=cmd,
            exit_code=proc.returncode,
            passed=proc.returncode == 0,
            output_head=text[-3000:],
            duration_s=duration,
            ran=True,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:  # noqa: BLE001
            pass
        return TestEvidence(command=cmd, exit_code=None, passed=False, output_head="timed out", ran=True)
