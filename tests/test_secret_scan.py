"""P0.1 secret-prevention gate tests (AIB-001 §5) — adversarial, hermetic.

The blueprint's acceptance test: an intentionally introduced synthetic
non-production secret must be COMMIT BLOCKED; the CI scan must FAIL on the
same content; placeholder-shaped fixtures (the repo's own test literals)
must PASS.  Everything runs in tmp repos — no live keys, no network.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCANNER = Path(__file__).resolve().parents[1] / "scripts" / "scan-secrets.py"

# Synthetic but valid-format secrets are built from fragments so the
# committed source never contains a contiguous secret-shaped token — the
# tree scan would otherwise flag this very file as a fixture leak (the
# same concatenation pattern the wrongness conflict-probe test uses).
TVLY_FAKE = "tvly-dev-" + "AbCdEf1234GhIjKl" + "5678MnOpQrStUvWxYz"

AWS_FAKE = "AKIA" + "IOSFODNN7EXAMPLE"
PEM_FAKE = "-----BEGIN " + "RSA PRIVATE KEY-----"


def _run(*args: str, cwd: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(__import__("os").environ)
    env.pop("MSB_SECRET_SCAN_OFF", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCANNER), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t.t")
    _git(r, "config", "user.name", "t")
    return r


def _stage_file(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", name)


# --- Staged mode: the blueprint's core acceptance test ---------------------


def test_synthetic_tavily_key_blocks_staged(repo: Path) -> None:
    """A synthetic non-production Tavily-shaped key must block the commit."""
    _stage_file(repo, "settings.json", '{"key": "' + TVLY_FAKE + '"}\n')
    res = _run("--staged", cwd=repo)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "BLOCKED" in res.stderr
    assert "tavily" in res.stderr


def test_synthetic_aws_key_blocks_staged(repo: Path) -> None:
    _stage_file(repo, "creds.txt", AWS_FAKE + "\n")
    res = _run("--staged", cwd=repo)
    assert res.returncode == 1
    assert "aws" in res.stderr


def test_synthetic_private_key_blocks_staged(repo: Path) -> None:
    _stage_file(repo, "id_rsa", PEM_FAKE + "MIIEow\n")
    res = _run("--staged", cwd=repo)
    assert res.returncode == 1
    assert "private-key" in res.stderr


# --- Placeholders must pass (the repo's own fixtures are shaped this way) --


def test_placeholder_single_class_passes(repo: Path) -> None:
    """tvly-dev-AAAA… (all one class) is the wrongness test's fake — passes."""
    _stage_file(repo, "settings.json", '{"key": "tvly-dev-AAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}\n')
    res = _run("--staged", cwd=repo)
    assert res.returncode == 0, res.stdout + res.stderr


def test_short_sk_passes(repo: Path) -> None:
    """sk-test / sk-local-noauth are the repo's provider-test fixtures."""
    _stage_file(repo, "settings.py", 'api_key = "sk-test"\n')
    res = _run("--staged", cwd=repo)
    assert res.returncode == 0, res.stdout + res.stderr


def test_clean_staged_passes(repo: Path) -> None:
    _stage_file(repo, "hello.py", "print('hello')\n")
    res = _run("--staged", cwd=repo)
    assert res.returncode == 0, res.stdout + res.stderr


# --- Tree mode: the CI forcing function ------------------------------------


def test_tree_mode_catches_committed_secret(repo: Path) -> None:
    """A secret committed to history must fail the --tree scan."""
    _stage_file(repo, "settings.local.json", '{"key": "' + TVLY_FAKE + '"}\n')
    _git(repo, "commit", "-q", "-m", "add leaked key")
    res = _run("--tree", cwd=repo)
    assert res.returncode == 1
    assert "BLOCKED" in res.stderr


def test_tree_mode_clean_after_removal(repo: Path) -> None:
    _stage_file(repo, "settings.local.json", '{"key": "' + TVLY_FAKE + '"}\n')
    _git(repo, "commit", "-q", "-m", "add leaked key")
    _git(repo, "rm", "-q", "--cached", "settings.local.json")
    _git(repo, "commit", "-q", "-m", "untrack leaked key")
    res = _run("--tree", cwd=repo)
    assert res.returncode == 0, res.stdout + res.stderr


# --- Overrides are explicit only -------------------------------------------


def test_pragma_allowlist_allows_staged(repo: Path) -> None:
    _stage_file(repo, "fixture.json", '{"key": "' + TVLY_FAKE + '"} # pragma: allowlist-secret\n')
    res = _run("--staged", "--allow-pragma", cwd=repo)
    assert res.returncode == 0, res.stdout + res.stderr
    # Without the flag the pragma is ignored — still blocked.
    blocked = _run("--staged", cwd=repo)
    assert blocked.returncode == 1


def test_env_override_is_explicit(repo: Path) -> None:
    _stage_file(repo, "settings.json", '{"key": "' + TVLY_FAKE + '"}\n')
    res = _run("--staged", cwd=repo, env_extra={"MSB_SECRET_SCAN_OFF": "1"})
    assert res.returncode == 0
    assert "bypassed" in res.stdout