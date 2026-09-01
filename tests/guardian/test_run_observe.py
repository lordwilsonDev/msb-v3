from __future__ import annotations

import json
import os
from pathlib import Path

from msb_v3.guardian.config import GuardianConfig
from msb_v3.guardian.run import EXIT_ESCALATE, EXIT_OK, execute


def test_clean_repo_reaches_reasoning_and_falls_back_without_claude(config_file: Path) -> None:
    # substrate = "sdk" in the fixture -> classify() returns a deterministic escalation
    result, code = execute(config_file, dry_run=True)
    assert result.decision == "ESCALATE"
    assert result.escalations[0].reason == "CAPABILITY_UNAVAILABLE"
    assert code == EXIT_ESCALATE
    cfg = GuardianConfig.load(config_file)
    run_dir = cfg.ledger.local_state_dir / "dry-run" / "ledger" / result.run_id
    assert (run_dir / "run.json").is_file()
    assert (run_dir / "forensics.json").is_file()
    assert (run_dir / "run.md").is_file()


def test_dirty_tree_escalates_before_reasoning(config_file: Path) -> None:
    cfg = GuardianConfig.load(config_file)
    (cfg.repo_path / "unexpected.py").write_text("x = 1\n", encoding="utf-8")
    result, code = execute(config_file, dry_run=True)
    assert result.decision == "ESCALATE"
    assert result.escalations[0].reason == "AMBIGUOUS_WORKING_TREE"
    assert code == EXIT_ESCALATE
    inbox = cfg.ledger.local_state_dir / "dry-run" / "inbox"
    notes = list(inbox.glob("S-AOS-ESCALATION_*AMBIGUOUS-WORKING-TREE.md"))
    assert len(notes) == 1


def test_ignorable_dirt_does_not_escalate(config_file: Path) -> None:
    cfg = GuardianConfig.load(config_file)
    (cfg.repo_path / "throwaway.archive.md").write_text("x", encoding="utf-8")
    result, _ = execute(config_file, dry_run=True)
    # still ESCALATE, but for the reasoning-fallback reason, NOT the dirty tree
    assert result.escalations[0].reason == "CAPABILITY_UNAVAILABLE"


def test_staged_only_tree_is_not_ambiguous(config_file: Path) -> None:
    import subprocess

    cfg = GuardianConfig.load(config_file)
    (cfg.repo_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='2'\n", encoding="utf-8")
    subprocess.run(["git", "add", "pyproject.toml"], cwd=cfg.repo_path, check=True, capture_output=True)
    result, code = execute(config_file, dry_run=True)
    assert result.decision == "PROPOSE"
    assert result.escalations[0].reason == "STAGED_PENDING_COMMIT"
    assert result.escalations[0].blocking is False
    assert code == EXIT_OK


def test_staged_plus_unstaged_still_ambiguous(config_file: Path) -> None:
    import subprocess

    cfg = GuardianConfig.load(config_file)
    (cfg.repo_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='2'\n", encoding="utf-8")
    subprocess.run(["git", "add", "pyproject.toml"], cwd=cfg.repo_path, check=True, capture_output=True)
    (cfg.repo_path / "unexpected.py").write_text("x = 1\n", encoding="utf-8")
    result, code = execute(config_file, dry_run=True)
    assert result.escalations[0].reason == "AMBIGUOUS_WORKING_TREE"
    assert code == EXIT_ESCALATE


def test_lock_contention(config_file: Path) -> None:
    cfg = GuardianConfig.load(config_file)
    d = cfg.ledger.local_state_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / "lock.json").write_text(json.dumps({"pid": os.getpid(), "run_id": "other"}), encoding="utf-8")
    result, code = execute(config_file, dry_run=True)
    assert result.escalations[0].reason == "CONCURRENT_GUARDIAN"
    assert code == EXIT_ESCALATE


def test_kpi_row_appended(config_file: Path) -> None:
    cfg = GuardianConfig.load(config_file)
    execute(config_file, dry_run=True)
    execute(config_file, dry_run=True)
    jsonl = cfg.ledger.local_state_dir / "dry-run" / "kpi" / "kpi.jsonl"
    rows = [json.loads(x) for x in jsonl.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 2
    assert all(r["decision"] == "ESCALATE" for r in rows)
    assert (cfg.ledger.local_state_dir / "dry-run" / "kpi" / "kpi.md").is_file()
    _ = EXIT_OK  # referenced so the import is meaningful across the module


def test_reasoning_provider_error_is_capability_unavailable(config_file, monkeypatch) -> None:
    """A `claude -p` envelope with is_error:true (quota/auth/api_error) -> a
    clean CAPABILITY_UNAVAILABLE escalation, not REASONING_UNVALIDATED."""
    import subprocess as sp

    from msb_v3.guardian import reasoning as rz
    from msb_v3.guardian.config import GuardianConfig

    cfg = GuardianConfig.load(config_file)
    # force the claude_cli path
    object.__setattr__(cfg.reasoning, "substrate", "claude_cli")

    class _P:
        returncode = 0
        stdout = '{"is_error": true, "api_error_status": 400, "result": "Credit balance is too low"}'
        stderr = ""

    monkeypatch.setattr(rz.shutil, "which", lambda _b: "/usr/bin/true")
    monkeypatch.setattr(sp, "run", lambda *a, **k: _P())
    out = rz.classify(cfg, "guardian-x", {"run_id": "guardian-x"})
    assert out.decision == "ESCALATE"
    assert out.escalations[0].reason == "CAPABILITY_UNAVAILABLE"
    assert "Credit balance" in out.escalations[0].detail
