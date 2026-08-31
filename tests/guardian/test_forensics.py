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


def test_state_hash_changes_with_real_dirt(config_file: Path) -> None:
    cfg = GuardianConfig.load(config_file)
    h1 = collect(cfg, "r1")["start_state_hash"]
    (cfg.repo_path / "real_new.py").write_text("x = 1\n", encoding="utf-8")
    h2 = collect(cfg, "r2")["start_state_hash"]
    assert h1 != h2
