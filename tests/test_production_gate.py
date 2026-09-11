"""Focused contract tests for the production-gate aggregator."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import production_gate as gate  # noqa: E402


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("pyproject.toml", "MANIFEST.md"):
        (repo / name).write_text("placeholder\n", encoding="utf-8")
    return repo


def _leg(name: str, classification: str = "required", status_rc: int = 0) -> gate.Leg:
    def runner(repo: Path):
        return status_rc, "secret output", "", []

    return gate.Leg(name, classification, runner)


def test_catalog_names_are_unique_and_status_vocabulary_is_closed() -> None:
    legs = gate.catalog()
    names = [leg.name for leg in legs]
    assert len(names) == len(set(names))
    assert gate.STATUSES == {"PASS", "FAIL", "CONDITIONAL", "WAIVED", "NOT_IMPLEMENTED"}


def test_make_target_wires_repository_owned_runner() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "production-gate:" in makefile
    assert "scripts/production_gate.py $(ARGS)" in makefile


def test_required_failure_controls_overall_verdict() -> None:
    results = [
        {"name": "required", "class": "required", "status": "FAIL"},
        {"name": "conditional", "class": "conditional", "status": "CONDITIONAL"},
    ]
    assert gate.overall_verdict(results) == "FAIL"


def test_conditional_result_is_explicit_not_failure() -> None:
    results = [{"name": "docker", "class": "conditional", "status": "CONDITIONAL"}]
    assert gate.overall_verdict(results) == "CONDITIONAL"


def test_waiver_requires_conditional_leg_and_reason() -> None:
    legs = [_leg("docker", "conditional"), _leg("lint", "required")]
    assert gate.parse_waivers(legs, ["docker"], ["daemon unavailable"]) == {
        "docker": "daemon unavailable"
    }
    with pytest.raises(ValueError, match="only conditional"):
        gate.parse_waivers(legs, ["lint"], ["not now"])
    with pytest.raises(ValueError, match="cannot be empty"):
        gate.parse_waivers(legs, ["docker"], ["  "])
    with pytest.raises(ValueError, match="one following"):
        gate.parse_waivers(legs, ["docker"], [])


def test_secret_redaction_removes_values_and_key_shapes() -> None:
    env = {"FAKE_API_KEY": "AbC123secret"}
    text = "key=AbC123secret token=sk-AbC123456789"
    redacted = gate.redact(text, env)
    assert "AbC123secret" not in redacted
    assert "sk-AbC123456789" not in redacted
    assert "[REDACTED]" in redacted


def test_identity_contains_reproducibility_fields(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    result = gate.identity(repo)
    assert result["commit"]
    assert result["branch"]
    assert result["dirty"] is False
    assert "python_version" in result
    assert set(result["lock_hashes"]) == {"runtime", "dev"}
    # Local-only after the frontier retirement (D1, 2026-09-09).
    assert set(result["models"]) == {"chat", "embedding"}


def test_write_manifest_is_atomic_and_machine_readable(tmp_path: Path) -> None:
    manifest = {"schema_version": 1, "gate_run_id": "test-run", "overall_verdict": "FAIL"}
    path = gate.write_manifest(tmp_path, manifest)
    assert json.loads(path.read_text()) == manifest
    assert not list(tmp_path.glob("*.tmp"))


def test_execute_records_not_implemented_and_follow_up_blockers(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)

    def not_implemented_runner(_repo: Path):
        return gate.not_implemented_leg(
            "standalone `msb-ledger verify --ledger ... --expected-root ...` contract is not implemented"
        )

    monkeypatch.setattr(
        gate,
        "catalog",
        lambda: [gate.Leg("standalone-ledger-verifier", "required", not_implemented_runner)],
    )
    rc, path, manifest = gate.execute(repo, tmp_path / "artifacts", {})
    assert rc == 1
    assert manifest["overall_verdict"] == "FAIL"
    assert manifest["legs"][0]["status"] == "NOT_IMPLEMENTED"
    assert manifest["follow_up_blockers"] == gate.FOLLOW_UP_BLOCKERS
    assert manifest["profile"] == "strict"
    assert path.is_file()


def test_candidate_profile_allows_conditional_but_not_required_failure(tmp_path: Path, monkeypatch, capsys) -> None:
    manifest = {
        "overall_verdict": "CONDITIONAL",
        "legs": [],
        "follow_up_blockers": {},
    }
    manifest_path = tmp_path / "gate.json"
    monkeypatch.setattr(
        gate,
        "execute",
        lambda repo, output_dir, waivers, *, profile="strict", invocation=None: (1, manifest_path, manifest),
    )
    assert gate.main(["--profile", "candidate"]) == 0
    assert "verdict=CONDITIONAL" in capsys.readouterr().out

    manifest["overall_verdict"] = "FAIL"
    assert gate.main(["--profile", "candidate"]) == 1
