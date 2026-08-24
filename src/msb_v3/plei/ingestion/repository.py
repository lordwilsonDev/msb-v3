"""Repository ingestion — git state, commit history, branches.

Read-only; never modifies the repo under inspection.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from msb_v3.plei.provenance import Provenance, Provenanced


@dataclass(slots=True)
class RepositoryFacts:
    """Git-derived facts about the project."""

    root: Provenanced = field(default_factory=Provenanced.unknown)
    branch: Provenanced = field(default_factory=Provenanced.unknown)
    commit_count: Provenanced = field(default_factory=Provenanced.unknown)
    last_commit_hash: Provenanced = field(default_factory=Provenanced.unknown)
    last_commit_message: Provenanced = field(default_factory=Provenanced.unknown)
    last_commit_date: Provenanced = field(default_factory=Provenanced.unknown)
    dirty: Provenanced = field(default_factory=Provenanced.unknown)
    recent_commits: Provenanced = field(default_factory=Provenanced.unknown)


def _run(cmd: list[str], cwd: str | Path) -> tuple[str, str]:
    """Run a git command, return (stdout, stderr) or raise."""
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=10)
    return proc.stdout.strip(), proc.stderr.strip()


def ingest_repository(project_root: str | Path) -> RepositoryFacts:
    """Ingest git state from the project root.

    Gracefully degrades: if the directory is not a git repo, every field
    returns UNKNOWN with the reason in ``source``.
    """
    root = Path(project_root).resolve()
    facts = RepositoryFacts(root=Provenanced.observed(str(root), "posix path"))

    # Git discovery
    try:
        if not (root / ".git").exists() and not (root / ".git").is_file():
            raise FileNotFoundError("no .git at project root")
    except FileNotFoundError as exc:
        for f in ("branch", "commit_count", "last_commit_hash", "last_commit_message",
                   "last_commit_date", "dirty", "recent_commits"):
            setattr(facts, f, Provenanced(value=None, provenance=Provenance.UNKNOWN,
                                           source=f"ingestion/repository: {exc}"))
        return facts

    source_tag = f"ingestion/repository (git, {root.name})"

    # Branch
    try:
        out, _ = _run(["git", "branch", "--show-current"], root)
        facts.branch = Provenanced.observed(out or "detached HEAD", source_tag)
    except Exception:
        facts.branch = Provenanced(value=None, provenance=Provenance.UNKNOWN, source=source_tag)

    # Commit count
    try:
        out, _ = _run(["git", "rev-list", "--count", "HEAD"], root)
        facts.commit_count = Provenanced.observed(int(out) if out.isdigit() else 0, source_tag)
    except Exception:
        facts.commit_count = Provenanced(value=0, provenance=Provenance.INFERRED, source=source_tag)

    # Last commit
    try:
        out_hash, _ = _run(["git", "log", "-1", "--format=%H"], root)
        out_msg, _ = _run(["git", "log", "-1", "--format=%s"], root)
        out_date, _ = _run(["git", "log", "-1", "--format=%ai"], root)
        facts.last_commit_hash = Provenanced.observed(out_hash, source_tag)
        facts.last_commit_message = Provenanced.observed(out_msg, source_tag)
        facts.last_commit_date = Provenanced.observed(out_date, source_tag)
    except Exception:
        facts.last_commit_hash = Provenanced(value=None, provenance=Provenance.UNKNOWN, source=source_tag)
        facts.last_commit_message = Provenanced(value=None, provenance=Provenance.UNKNOWN, source=source_tag)
        facts.last_commit_date = Provenanced(value=None, provenance=Provenance.UNKNOWN, source=source_tag)

    # Dirty state
    try:
        out, _ = _run(["git", "status", "--short"], root)
        facts.dirty = Provenanced.observed(bool(out), source_tag)
    except Exception:
        facts.dirty = Provenanced(value=None, provenance=Provenance.UNKNOWN, source=source_tag)

    # Recent commits (last 10)
    try:
        out, _ = _run(["git", "log", "--oneline", "-10"], root)
        facts.recent_commits = Provenanced.observed(out.split("\n") if out else [], source_tag)
    except Exception:
        facts.recent_commits = Provenanced.observed([], source_tag)

    return facts


def repo_facts_as_dict(facts: RepositoryFacts) -> dict[str, Any]:
    return {k: getattr(facts, k).as_dict() for k in (
        "root", "branch", "commit_count", "last_commit_hash",
        "last_commit_message", "last_commit_date", "dirty", "recent_commits")}