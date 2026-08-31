"""Deterministic forensics sweep (doc 4 §5).

No model is involved here. Every probe is best-effort: a missing tool or an
unreadable file is recorded as ``NOT_RUN`` / ``UNKNOWN``, never raised. The
output is a plain ``dict`` ready to be JSON-serialised and handed to the
reasoning step.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import GuardianConfig

_GIT_TIMEOUT = 15
_SUBPROC_TIMEOUT = 8


def _in_venv() -> bool:
    return sys.prefix != sys.base_prefix or bool(os.environ.get("VIRTUAL_ENV"))


def _run(
    args: list[str], cwd: Path, timeout: int = _SUBPROC_TIMEOUT, *, strip: bool = True
) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
        out = p.stdout.strip() if strip else p.stdout
        return p.returncode, out, p.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:  # pragma: no cover - env dependent
        return 127, "", str(exc)


def _is_ignorable(path: str, globs: list[str]) -> bool:
    norm = path.rstrip("/")
    for g in globs:
        gg = g.rstrip("/")
        if fnmatch.fnmatch(norm, gg) or fnmatch.fnmatch(norm, f"{gg}/*") or norm == gg:
            return True
        if norm.startswith(f"{gg}/"):
            return True
    return False


def _porcelain_entries(raw: str) -> list[tuple[str, str]]:
    """Return (xy, path) pairs from ``git status --porcelain`` (v1) output.

    Layout is fixed-column: 2 status chars, a space, then the path. Callers
    MUST pass the raw (un-stripped) stdout — a global ``strip`` shifts the
    leading space off the first line and corrupts the column parse.
    """
    out: list[tuple[str, str]] = []
    for line in raw.split("\n"):
        line = line.rstrip("\n\r")
        if len(line) < 4:
            continue
        xy, path = line[:2], line[3:].strip('"')
        if " -> " in path:  # rename: keep the destination
            path = path.split(" -> ", 1)[1].strip('"')
        out.append((xy, path))
    return out


def _git_block(cfg: GuardianConfig, repo: Path) -> dict[str, object]:
    rc_head, head, _ = _run(["git", "rev-parse", "HEAD"], repo, _GIT_TIMEOUT)
    _, branch, _ = _run(["git", "branch", "--show-current"], repo, _GIT_TIMEOUT)
    _, last_commit, _ = _run(
        ["git", "log", "-1", "--format=%cI %s"], repo, _GIT_TIMEOUT
    )
    _, porcelain, _ = _run(
        ["git", "status", "--porcelain"], repo, _GIT_TIMEOUT, strip=False
    )
    rc_up, updata, _ = _run(
        ["git", "rev-list", "--left-right", "--count", "HEAD...@{u}"], repo, _GIT_TIMEOUT
    )
    ahead = behind = 0
    have_upstream = rc_up == 0 and "\t" in updata
    if have_upstream:
        left, _, right = updata.partition("\t")
        ahead, behind = int(left or 0), int(right or 0)

    globs = cfg.fingerprint.ignorable_globs
    entries = _porcelain_entries(porcelain)
    modified: list[str] = []
    untracked: list[str] = []
    ignored_out: list[str] = []
    for xy, path in entries:
        if _is_ignorable(path, globs):
            ignored_out.append(path)
            continue
        if xy == "??":
            untracked.append(path)
        else:
            modified.append(path)

    clean_after_filter = not modified and not untracked

    return {
        "head": head if rc_head == 0 else "UNKNOWN",
        "branch": branch or "UNKNOWN",
        "last_commit": last_commit or "UNKNOWN",
        "upstream": {
            "tracking": have_upstream,
            "ahead": ahead,
            "behind": behind,
            "diverged": ahead > 0 and behind > 0,
        },
        "working_tree": {
            "clean_after_filter": clean_after_filter,
            "modified": sorted(modified),
            "untracked": sorted(untracked),
            "ignored_by_config": sorted(ignored_out),
            "ownership_attributable_to_guardian": False,
        },
    }


def _state_hash(repo: Path, git_block: dict[str, object]) -> str:
    head = str(git_block.get("head", ""))
    wt = git_block.get("working_tree", {})
    parts = [head]
    if isinstance(wt, dict):
        parts += [f"M {p}" for p in wt.get("modified", [])]  # type: ignore[union-attr]
        parts += [f"?? {p}" for p in wt.get("untracked", [])]  # type: ignore[union-attr]
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _mtime(p: Path) -> str | None:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _manifests_block(repo: Path) -> dict[str, object]:
    files: dict[str, str | None] = {}
    for name in (
        "pyproject.toml",
        "requirements-dev.lock",
        "requirements-runtime.lock",
        "uv.lock",
        "poetry.lock",
    ):
        fp = repo / name
        if fp.exists():
            files[name] = _mtime(fp)

    drift = "UNKNOWN"
    pj = repo / "pyproject.toml"
    lock = repo / "requirements-runtime.lock"
    if pj.exists() and lock.exists():
        drift = (
            "SUSPECTED (pyproject newer than lock; mtime heuristic only)"
            if pj.stat().st_mtime > lock.stat().st_mtime
            else "none (lock newer than pyproject)"
        )

    outdated: object = "UNKNOWN (no active venv in forensics shell)"
    if _in_venv():
        rc, out, _ = _run(
            [sys.executable, "-m", "pip", "list", "--outdated", "--format=json"], repo
        )
        if rc == 0 and out.startswith("["):
            outdated = out  # raw JSON string; the reasoning step can parse if needed

    vuln: object = "NOT_RUN (pip-audit not on PATH)"
    audit_bin = shutil.which("pip-audit")
    if audit_bin is not None and _in_venv():
        rc_a, out_a, _ = _run([audit_bin, "-f", "json"], repo)
        if rc_a == 0 and out_a.startswith("{"):
            vuln = out_a

    return {
        "files": files,
        "lock_drift_heuristic": drift,
        "outdated_runtime": outdated,
        "vuln_findings": vuln,
    }


def _tests_block(repo: Path) -> dict[str, object]:
    cov = repo / ".coverage"
    cache = repo / ".pytest_cache"
    lastfailed = cache / "v" / "cache" / "lastfailed"
    failed: list[str] = []
    if lastfailed.exists():
        try:
            import json

            data = json.loads(lastfailed.read_text(encoding="utf-8"))
            failed = sorted(str(k) for k in data)
        except (OSError, ValueError):
            failed = []
    return {
        "this_cycle": "NOT_RUN",
        "coverage_mtime": _mtime(cov),
        "pytest_cache_mtime": _mtime(cache),
        "lastfailed": failed,
        "note": "lastfailed reflects the cache's last run, which may pre-date the latest suite run",
    }


def _static_block(repo: Path) -> dict[str, object]:
    return {
        "ruff_cache_mtime": _mtime(repo / ".ruff_cache"),
        "mypy_cache_mtime": _mtime(repo / ".mypy_cache"),
        "this_cycle": "NOT_RUN",
    }


def _logs_block(repo: Path) -> dict[str, object]:
    logs = repo / "logs"
    observed: list[dict[str, object]] = []
    if logs.is_dir():
        entries = sorted(
            (f for f in logs.iterdir() if f.is_file()),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for f in entries[:12]:
            size = f.stat().st_size
            observed.append(
                {
                    "file": f"logs/{f.name}",
                    "size_bytes": size,
                    "last_write": _mtime(f),
                    "large_active": size > 1_000_000,
                }
            )
    return {"sampled": False, "observed": observed}


def _resources_block(repo: Path, cfg: GuardianConfig) -> dict[str, object]:
    flags: list[str] = []
    try:
        usage = shutil.disk_usage(repo)
        free_pct = round(usage.free / usage.total * 100, 1)
    except OSError:
        free_pct = -1.0
    if 0 <= free_pct < cfg.resources.disk_free_pct_min:
        flags.append("disk_free_below_min")
    flags.append("disk_reading_unverified")  # doc 1 §4 — vantage point not guaranteed
    try:
        load1 = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):  # pragma: no cover - platform dependent
        load1 = -1.0
    if load1 > cfg.resources.load_1m_max:
        flags.append("load_over_max")
    return {"disk_free_pct": free_pct, "load_1m": load1, "flags": flags}


def _secret_guard(repo: Path, cfg: GuardianConfig) -> dict[str, object]:
    present = any((repo / s).exists() for s in cfg.fingerprint.secret_paths if "*" not in s)
    return {"secret_paths_present": present, "secret_paths_read": False}


def collect(cfg: GuardianConfig, run_id: str) -> dict[str, object]:
    """Assemble the full forensics bundle for one run."""
    repo = cfg.repo_path.resolve()
    git_block = _git_block(cfg, repo)
    bundle: dict[str, object] = {
        "run_id": run_id,
        "mode": cfg.mode,
        "repo": repo.name,
        "repo_path": str(repo),
        "collected_at": datetime.now(tz=timezone.utc).isoformat(),
        "secret_guard": _secret_guard(repo, cfg),
        "git": git_block,
        "start_state_hash": _state_hash(repo, git_block),
        "manifests": _manifests_block(repo),
        "tests": _tests_block(repo),
        "static": _static_block(repo),
        "logs": _logs_block(repo),
        "resources": _resources_block(repo, cfg),
    }
    return bundle
