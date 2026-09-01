from __future__ import annotations

from pathlib import Path

from msb_v3.guardian.config import GuardianConfig
from msb_v3.guardian.forensics import _is_ignorable, _porcelain_entries, collect


def test_porcelain_parse_keeps_leading_dot_on_first_line() -> None:
    # regression: a global strip() on stdout ate the first line's leading
    # space and turned ".plei/x" into "plei/x"
    raw = " M .plei/calibration.jsonl\n M CLAUDE.md\n?? scratch/\n"
    assert _porcelain_entries(raw) == [
        (" M", ".plei/calibration.jsonl"),
        (" M", "CLAUDE.md"),
        ("??", "scratch/"),
    ]


def test_porcelain_rename_keeps_destination() -> None:
    raw = "R  old/name.py -> new/name.py\n"
    assert _porcelain_entries(raw) == [("R ", "new/name.py")]


def test_is_ignorable_matches_dir_and_glob() -> None:
    globs = ["scratch/", ".plei/", "*.archive.md"]
    assert _is_ignorable("scratch/foo.txt", globs)
    assert _is_ignorable("scratch", globs)
    assert _is_ignorable(".plei/calibration.jsonl", globs)
    assert _is_ignorable("CLAUDE.archive.md", globs)
    assert not _is_ignorable("src/msb_v3/app.py", globs)
    assert not _is_ignorable("CLAUDE.md", globs)


def test_clean_repo_bundle(config_file: Path) -> None:
    cfg = GuardianConfig.load(config_file)
    b = collect(cfg, "guardian-test")
    git = b["git"]
    assert isinstance(git, dict)
    assert git["working_tree"]["clean_after_filter"] is True
    assert str(b["start_state_hash"]).startswith("sha256:")
    assert b["secret_guard"]["secret_paths_read"] is False


def test_ignorable_untracked_is_filtered_but_real_untracked_is_not(config_file: Path) -> None:
    cfg = GuardianConfig.load(config_file)
    repo = cfg.repo_path
    (repo / "scratch").mkdir()
    (repo / "scratch" / "note.txt").write_text("x", encoding="utf-8")
    (repo / "NOTES.archive.md").write_text("x", encoding="utf-8")
    b = collect(cfg, "guardian-test")
    wt = b["git"]["working_tree"]  # type: ignore[index]
    assert wt["clean_after_filter"] is True
    # git reports an untracked dir as "scratch/", not per-file
    assert set(wt["ignored_by_config"]) >= {"scratch/", "NOTES.archive.md"}

    (repo / "real_new.py").write_text("x = 1\n", encoding="utf-8")
    b2 = collect(cfg, "guardian-test")
    wt2 = b2["git"]["working_tree"]  # type: ignore[index]
    assert wt2["clean_after_filter"] is False
    assert "real_new.py" in wt2["untracked"]


def test_git_calls_are_lockless() -> None:
    import inspect

    from msb_v3.guardian import forensics as fx

    src = inspect.getsource(fx._git)
    assert "--no-optional-locks" in src
    assert "GIT_OPTIONAL_LOCKS" in src


def test_staged_only_split(config_file: Path) -> None:
    import subprocess

    cfg = GuardianConfig.load(config_file)
    repo = cfg.repo_path
    (repo / "pyproject.toml").write_text("[project]\nname='x'\nversion='9'\n", encoding="utf-8")
    subprocess.run(["git", "add", "pyproject.toml"], cwd=repo, check=True, capture_output=True)
    wt = collect(cfg, "r")["git"]["working_tree"]  # type: ignore[index]
    assert wt["staged"] == ["pyproject.toml"]
    assert wt["unstaged"] == []
    assert wt["untracked"] == []
    assert wt["staged_only"] is True
    assert wt["clean_after_filter"] is False

    # add an unstaged edit on top -> no longer staged_only
    (repo / "requirements-runtime.lock").write_text("# changed\n", encoding="utf-8")
    wt2 = collect(cfg, "r2")["git"]["working_tree"]  # type: ignore[index]
    assert wt2["staged_only"] is False
    assert "requirements-runtime.lock" in wt2["unstaged"]


def test_state_hash_changes_with_real_dirt(config_file: Path) -> None:
    cfg = GuardianConfig.load(config_file)
    h1 = collect(cfg, "r1")["start_state_hash"]
    (cfg.repo_path / "real_new.py").write_text("x = 1\n", encoding="utf-8")
    h2 = collect(cfg, "r2")["start_state_hash"]
    assert h1 != h2


def test_g1_lock_drift_is_content_based(config_file: Path) -> None:
    from msb_v3.guardian.forensics import _lock_drift

    cfg = GuardianConfig.load(config_file)
    repo = cfg.repo_path
    (repo / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="9.9"\ndependencies = ["foo==1.2.3", "httpx==0.28.1"]\n'
        '[project.optional-dependencies]\nspeech = ["webrtcvad==2.0.10"]\n',
        encoding="utf-8",
    )
    # runtime pins present (extras-tolerant); speech group is ignored by design
    (repo / "requirements-runtime.lock").write_text(
        "foo==1.2.3\nhttpx[http2]==0.28.1\nbar==0.1\n", encoding="utf-8"
    )
    assert _lock_drift(repo).startswith("none")
    # drop a runtime pin -> real drift
    (repo / "requirements-runtime.lock").write_text("httpx[http2]==0.28.1\nbar==0.1\n", encoding="utf-8")
    d = _lock_drift(repo)
    assert d.startswith("DRIFT") and "foo==1.2.3" in d


def test_g2_stale_lastfailed_flag(config_file: Path, tmp_path: Path) -> None:
    import os
    import time

    cfg = GuardianConfig.load(config_file)
    repo = cfg.repo_path
    cache = repo / ".pytest_cache" / "v" / "cache"
    cache.mkdir(parents=True)
    (cache.parent.parent / "CACHEDIR.TAG").write_text("x", encoding="utf-8")
    (cache / "lastfailed").write_text('{"tests/x.py::t": true}', encoding="utf-8")
    old = time.time() - 3600
    os.utime(repo / ".pytest_cache", (old, old))
    (repo / ".coverage").write_text("cov", encoding="utf-8")  # fresh
    b = collect(cfg, "r")["tests"]  # type: ignore[index]
    assert b["lastfailed_stale"] is True
    assert "STALE" in b["note"]
