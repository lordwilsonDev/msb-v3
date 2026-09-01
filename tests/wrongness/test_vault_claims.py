"""M8: vault-authored claims — schema gate + batch run + authoring hook.

- ``claims_valid`` is the authoring hook: every claim JSON in a directory
  must parse through the engine's schema, so a malformed vault claim fails
  fast instead of silently producing a NOTE verdict.
- ``run-all`` runs every claim in a directory in one command (the vault
  claims home), and ``validate`` gives an author instant schema feedback.
- Underscore-prefixed files (the ``_TEMPLATE.json`` convention) are skipped
  by both the check and the batch runner.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from msb_v3.wrongness.__main__ import cmd_run_all, cmd_validate
from msb_v3.wrongness.checks import check_claims_valid


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


VALID_CLAIM = {
    "id": "vault-test-01",
    "statement": "a test claim",
    "domain": "test",
    "consequence": "low",
}


def test_claims_valid_accepts_schema_conformant_dir(temp_repo: Path) -> None:
    _write(temp_repo, "claims/vault-test-01.json", json.dumps(VALID_CLAIM))
    res = check_claims_valid(temp_repo, "claims")
    assert res.ok is True
    assert "1 claim(s)" in res.evidence
    assert any(link.path == "claims/vault-test-01.json" for link in res.links)


def test_claims_valid_flags_malformed(temp_repo: Path) -> None:
    _write(temp_repo, "claims/good.json", json.dumps(VALID_CLAIM))
    _write(temp_repo, "claims/bad.json", json.dumps({"statement": "no id field"}))
    res = check_claims_valid(temp_repo, "claims")
    assert res.ok is False
    assert "bad.json" in res.evidence


def test_claims_valid_ignores_underscore_template(temp_repo: Path) -> None:
    _write(temp_repo, "claims/vault-test-01.json", json.dumps(VALID_CLAIM))
    _write(
        temp_repo,
        "claims/_TEMPLATE.json",
        json.dumps({"id": "", "statement": ""}),  # placeholder, must be skipped
    )
    res = check_claims_valid(temp_repo, "claims")
    assert res.ok is True
    assert "1 claim(s)" in res.evidence


def test_claims_valid_missing_dir_is_inconclusive(temp_repo: Path) -> None:
    res = check_claims_valid(temp_repo, "nope")
    assert res.ok is None


def test_run_all_discovers_and_runs(temp_repo: Path, capsys) -> None:
    _write(temp_repo, "x", "1\n")
    (temp_repo / "x").chmod(0o644)
    with_check = {
        "id": "vault-file-mode",
        "statement": "x is 0600",
        "domain": "test",
        "consequence": "medium",
        "checks": [{"kind": "file_mode", "params": {"path": "x", "expected": "0600"}}],
    }
    _write(temp_repo, "claims/vault-file-mode.json", json.dumps(with_check))
    _write(temp_repo, "claims/vault-test-01.json", json.dumps(VALID_CLAIM))
    _write(temp_repo, "claims/_TEMPLATE.json", json.dumps({"id": "", "statement": ""}))
    out_dir = temp_repo / "out"
    rc = cmd_run_all(
        Namespace(claims_dir=str(temp_repo / "claims"), repo=str(temp_repo), out=str(out_dir))
    )
    assert rc == 0
    captured = capsys.readouterr().out
    assert "vault-file-mode" in captured
    assert "vault-test-01" in captured
    assert "_TEMPLATE" not in captured  # skipped
    # per-claim reports written, verdicts sharp (ESCALATE for the failing check)
    assert (out_dir / "vault-file-mode.md").exists()
    assert "ESCALATE" in (out_dir / "vault-file-mode.md").read_text(encoding="utf-8")


def test_run_all_missing_dir_errors(temp_repo: Path, capsys) -> None:
    rc = cmd_run_all(Namespace(claims_dir=str(temp_repo / "nope"), repo=str(temp_repo), out=None))
    assert rc == 1
    assert "not found" in capsys.readouterr().out


def test_validate_accepts_good_and_rejects_bad(temp_repo: Path, capsys) -> None:
    good = temp_repo / "good.json"
    good.write_text(json.dumps(VALID_CLAIM), encoding="utf-8")
    assert cmd_validate(Namespace(claim=str(good))) == 0
    assert "valid claim: vault-test-01" in capsys.readouterr().out

    bad = temp_repo / "bad.json"
    bad.write_text(json.dumps({"statement": "missing id"}), encoding="utf-8")
    assert cmd_validate(Namespace(claim=str(bad))) == 1
    assert "invalid claim" in capsys.readouterr().out
