"""scripts/lib/held_commits.sh: commits marked [do-not-push] must hold any push."""
from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lib" / "held_commits.sh"


def _git(cwd: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(cwd)}
    done = subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=cwd, capture_output=True, text=True, env=env, check=True)
    return done.stdout


def _commit(repo: Path, name: str, message: str) -> None:
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", message)


def _repo(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    remote.mkdir()
    work.mkdir()
    _git(remote, "init", "-q", "--bare")
    _git(work, "init", "-q", "-b", "main")
    _git(work, "remote", "add", "origin", str(remote))
    _commit(work, "base", "base")
    _git(work, "push", "-q", "-u", "origin", "main")
    return work


def _held(repo: Path) -> int:
    done = subprocess.run(["bash", str(SCRIPT), str(repo)], capture_output=True, text=True, check=True)
    return int(done.stdout.strip())


def test_nothing_unpushed_counts_zero(tmp_path: Path) -> None:
    assert _held(_repo(tmp_path)) == 0


def test_ordinary_commits_do_not_hold(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "a", "feat: something")
    assert _held(repo) == 0


def test_marked_commit_holds_the_push(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "a", "chore: daily gate evidence (BLOCKED) 2026-10-04 [do-not-push]")
    assert _held(repo) == 1


def test_a_later_ordinary_commit_does_not_release_an_earlier_marked_one(tmp_path: Path) -> None:
    """A branch is pushed as a prefix: the marked commit is an ancestor of the tip."""
    repo = _repo(tmp_path)
    _commit(repo, "a", "chore: evidence [do-not-push]")
    _commit(repo, "b", "audit: report")
    assert _held(repo) == 1


def test_a_pushed_marked_commit_no_longer_holds(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "a", "chore: evidence [do-not-push]")
    _git(repo, "push", "-q")
    assert _held(repo) == 0


def test_no_upstream_counts_zero_instead_of_failing(tmp_path: Path) -> None:
    repo = tmp_path / "lonely"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "a", "chore: evidence [do-not-push]")
    assert _held(repo) == 0  # unreadable range: callers fail closed elsewhere (the push has no upstream either)
