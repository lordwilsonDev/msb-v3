"""Tests for the self-maintenance audit (automation/audit.py) — the wake
cycle checks its own manifest, posts findings to the outbox only when the
picture changed."""

from __future__ import annotations

from msb_v3.automation.audit import run_audit
from msb_v3.automation.budget import BudgetLedger
from msb_v3.automation.manifest import Manifest
from msb_v3.automation.state import AutomationState
from msb_v3.core.config import settings
from msb_v3.wake.store import WakeStore


def test_healthy_system_is_silent(monkeypatch, tmp_path) -> None:
    # Force every provider unavailable → findings appear (honest), but the
    # second unchanged pass must not re-post.
    for attr in ("n8n_api_key", "ghl_api_key", "zapier_api_key", "make_webhook_url"):
        monkeypatch.setattr(settings, attr, "")
    wake = WakeStore(db_path=str(tmp_path / "wake.db"))
    result = run_audit(
        manifest=Manifest(path=str(tmp_path / "manifest.jsonl")),
        state=AutomationState(db_path=str(tmp_path / "state.db")),
        budget=BudgetLedger(db_path=str(tmp_path / "budget.db"), cap_usd=10.0),
        wake=wake,
        audit_path=tmp_path / "audit.json",
    )
    assert result["ok"] is True
    assert result["changed"] is True
    assert len(result["findings"]) >= 4  # four provider seams blocked-with-reason
    assert any(f["kind"] == "provider" for f in result["findings"])
    assert len(wake.outbox()) == 1
    assert wake.outbox()[0]["source"] == "audit"

    # Unchanged picture → nothing new posted, changed=False.
    result2 = run_audit(
        manifest=Manifest(path=str(tmp_path / "manifest.jsonl")),
        state=AutomationState(db_path=str(tmp_path / "state.db")),
        budget=BudgetLedger(db_path=str(tmp_path / "budget.db"), cap_usd=10.0),
        wake=wake,
        audit_path=tmp_path / "audit.json",
    )
    assert result2["changed"] is False
    assert len(wake.outbox()) == 1


def test_dead_hook_and_drift_findings(tmp_path) -> None:
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    state = AutomationState(db_path=str(tmp_path / "state.db"))
    entry = manifest.append(
        provider="self", name="broken", description="x", status="created",
        summary="registered", schedule="* * * * *",
        action={"type": "webhook_post", "url": "http://127.0.0.1:9/x", "payload": {}},
    )
    state.upsert(entry["id"], "* * * * *")
    state.mark_run(entry["id"], "FAILED", "RuntimeError: connection refused")
    state.upsert("auto-ghost", "* * * * *")  # state row with no manifest entry

    wake = WakeStore(db_path=str(tmp_path / "wake.db"))
    result = run_audit(
        manifest=manifest,
        state=state,
        budget=BudgetLedger(db_path=str(tmp_path / "budget.db"), cap_usd=10.0),
        wake=wake,
        audit_path=tmp_path / "audit.json",
    )
    kinds = {f["kind"] for f in result["findings"]}
    assert "dead_hook" in kinds
    assert "drift" in kinds
    assert result["changed"] is True
    out = wake.outbox()
    assert len(out) == 1
    assert "dead_hook" in out[0]["text"] and "drift" in out[0]["text"]
