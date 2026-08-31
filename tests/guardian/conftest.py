"""Hermetic fixtures for the Guardian suite — no live model, no network."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (repo / "requirements-runtime.lock").write_text("# lock\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


@pytest.fixture
def config_file(tmp_path: Path, temp_repo: Path) -> Path:
    """A config whose reasoning substrate is 'sdk' so classify() never shells out."""
    state = tmp_path / "state"
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Guardian system prompt.\n", encoding="utf-8")
    cfg = tmp_path / "guardian.toml"
    cfg.write_text(
        textwrap.dedent(
            f"""
            [guardian]
            mode = "OBSERVE"
            timebox_seconds = 300
            repo_path = "{temp_repo.as_posix()}"

            [fingerprint]
            ignorable_globs = ["scratch/", "*.archive.md"]
            secret_paths = [".env"]

            [resources]
            disk_free_pct_min = 1
            load_1m_max = 999

            [reasoning]
            substrate = "sdk"
            claude_bin = "claude"
            model = ""
            max_retries = 0
            system_prompt_path = "{prompt.as_posix()}"

            [ledger]
            vault_dir = "{(tmp_path / 'vault').as_posix()}"
            inbox_dir = "{(tmp_path / 'inbox').as_posix()}"
            local_state_dir = "{state.as_posix()}"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return cfg
