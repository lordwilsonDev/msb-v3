"""Tests for the automation manifest ledger (automation/manifest.py)."""

from __future__ import annotations

from msb_v3.automation.manifest import Manifest


def test_append_and_list(tmp_path) -> None:
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    manifest.append(provider="n8n", name="webhook echo", description="echo", status="dry_run", summary="plan")
    e2 = manifest.append(provider="ghl", name="lead followup", description="followup", status="created", summary="done")
    rows = manifest.list()
    assert len(rows) == 2
    assert rows[0]["id"] == e2["id"]  # newest first
    assert rows[1]["provider"] == "n8n"


def test_list_missing_file(tmp_path) -> None:
    manifest = Manifest(path=str(tmp_path / "nope" / "manifest.jsonl"))
    assert manifest.list() == []
