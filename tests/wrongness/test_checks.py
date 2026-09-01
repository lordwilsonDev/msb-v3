"""Deterministic checks: hermetic on temp repos, live re-detection on the tree.

The live tests are the point (the by-hand lesson: the strongest check is
often 5 lines of shell).  C4/C5/C6 were verified against the real tree in
the hardening audit — these assert the checks still catch them today.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from msb_v3.wrongness.checks import (
    check_call_sites,
    check_file_mode,
    check_porcelain,
    check_tracked_secret,
)

REPO = Path(__file__).resolve().parents[2]


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _commit_all(repo: Path) -> None:
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "wip"], cwd=repo, check=True, capture_output=True)


# --- hermetic -----------------------------------------------------------------


def test_call_sites_min_count(temp_repo: Path) -> None:
    _write(temp_repo, "src/a.py", "def ensure_schema():\n    pass\n")
    _write(temp_repo, "src/b.py", "import ensure_schema\n")
    _commit_all(temp_repo)
    # Two files reference it -> claim "shipped capability" holds
    assert check_call_sites(temp_repo, "ensure_schema", min_count=2).ok is True
    # Only its own definition -> dead code
    (temp_repo / "src" / "b.py").unlink()
    _commit_all(temp_repo)
    assert check_call_sites(temp_repo, "ensure_schema", min_count=2).ok is False


def test_call_sites_max_count(temp_repo: Path) -> None:
    _write(temp_repo, "src/a.py", "def f():\n    return 1\n")
    _commit_all(temp_repo)
    # A def IS a reference: n=1, within max=1 -> holds
    assert check_call_sites(temp_repo, "f", max_count=1).ok is True
    _write(temp_repo, "src/b.py", "from a import f\nx = f()\n")
    _commit_all(temp_repo)
    assert check_call_sites(temp_repo, "f", max_count=1).ok is False
    assert check_call_sites(temp_repo, "f", max_count=2).ok is True
    # A symbol with zero references is UNKNOWN, not a pass
    assert check_call_sites(temp_repo, "no_such_symbol", max_count=1).ok is None


def test_file_mode(temp_repo: Path) -> None:
    env = temp_repo / ".env"
    env.write_text("SECRET=1\n", encoding="utf-8")
    env.chmod(0o644)
    assert check_file_mode(temp_repo, ".env", "0600").ok is False
    assert check_file_mode(temp_repo, ".env", "0644").ok is True


def test_tracked_secret_scan(temp_repo: Path) -> None:
    _write(temp_repo, "settings.json", '{"key": "tvly-dev-AAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}')
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=temp_repo, check=True, capture_output=True)
    res = check_tracked_secret(temp_repo, r"tvly-[A-Za-z0-9_-]{20,}")
    assert res.ok is False
    assert "settings.json" in res.evidence


def test_tracked_secret_clean(temp_repo: Path) -> None:
    _write(temp_repo, "settings.json", '{"key": "no-secret-here"}')
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=temp_repo, check=True, capture_output=True)
    assert check_tracked_secret(temp_repo, r"tvly-[A-Za-z0-9_-]{20,}").ok is True


def test_porcelain_staged_is_healthy(temp_repo: Path) -> None:
    _write(temp_repo, "a.txt", "x\n")
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=temp_repo, check=True, capture_output=True)
    assert check_porcelain(temp_repo).ok is True  # staged-only is the healthy pre-commit state


def test_porcelain_unstaged_is_dirty(temp_repo: Path) -> None:
    _write(temp_repo, "a.txt", "x\n")
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=temp_repo, check=True, capture_output=True)
    (temp_repo / "a.txt").write_text("y\n", encoding="utf-8")
    assert check_porcelain(temp_repo).ok is False


# --- live re-detection on the real tree ---------------------------------------


@pytest.mark.skipif(not (REPO / ".env").exists(), reason="msb-v3 .env absent")
def test_live_c6_env_mode_still_wrong() -> None:
    """H4 sub-claim: '.env is 0600' — audit found 0644. Still wrong?"""
    res = check_file_mode(REPO, ".env", "0600")
    assert res.ok is False, f".env should still be non-0600: {res.evidence}"


def test_live_c5_ensure_schema_now_wired() -> None:
    """H9 is FIXED (44b3685, 2026-08-31): ensure_schema is now called from
    migrations + tests. The check must now PASS — the engine's re-detection
    is sensitive to the fix, which is exactly the desired behavior."""
    res = check_call_sites(REPO, "ensure_schema", min_count=2)
    assert res.ok is True, f"ensure_schema should now have >=2 call sites: {res.evidence}"


def test_live_c4_secret_scan() -> None:
    """H4: no plaintext secret in the repo. The scan must still flag it."""
    res = check_tracked_secret(REPO, r"tvly-[A-Za-z0-9_-]{20,}")
    assert res.ok is False, f"tracked secret should still be present: {res.evidence}"
