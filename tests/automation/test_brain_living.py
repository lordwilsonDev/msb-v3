"""Tests for the brain's living-automation path (automation/brain.py) —
recipes register with the dispatcher state, dry-run by default, approval
enables them, bad schedules fail closed. Zero LLM, zero platform spend."""

from __future__ import annotations

from msb_v3.automation.brain import create_automation, plan_automation
from msb_v3.automation.manifest import Manifest
from msb_v3.automation.state import AutomationState


def test_recipe_plans_without_llm() -> None:
    plan = plan_automation("every 30 minutes, post a heartbeat to http://127.0.0.1:5678/webhook/msb-ping", llm=lambda m: "unused")
    assert plan["provider"] == "self"
    assert plan["schedule"] == "*/30 * * * *"


def test_living_automation_dry_runs_by_default(tmp_path) -> None:
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    state = AutomationState(db_path=str(tmp_path / "state.db"))
    plan = {
        "provider": "self",
        "name": "heartbeat",
        "description": "post a heartbeat",
        "schedule": "*/30 * * * *",
        "action": {"type": "webhook_post", "url": "http://127.0.0.1:5678/webhook/msb-ping", "payload": {}},
    }
    result = create_automation(plan, approve=False, manifest=manifest, state=state)
    assert result["status"] == "dry_run"
    assert state.list() == []  # nothing registered without approval


def test_living_automation_registers_on_approval(tmp_path) -> None:
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    state = AutomationState(db_path=str(tmp_path / "state.db"))
    plan = {
        "provider": "self",
        "name": "heartbeat",
        "description": "post a heartbeat",
        "schedule": "*/30 * * * *",
        "action": {"type": "webhook_post", "url": "http://127.0.0.1:5678/webhook/msb-ping", "payload": {}},
    }
    result = create_automation(plan, approve=True, manifest=manifest, state=state)
    assert result["status"] == "created"
    entry = result["entry"]
    rows = state.list()
    assert len(rows) == 1
    assert rows[0]["automation_id"] == entry["id"]
    assert rows[0]["enabled"] is True


def test_invalid_schedule_fails_closed(tmp_path) -> None:
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    state = AutomationState(db_path=str(tmp_path / "state.db"))
    plan = {
        "provider": "self",
        "name": "bad",
        "description": "x",
        "schedule": "99 99 99 99 99",
        "action": {"type": "webhook_post", "url": "http://127.0.0.1:9/x", "payload": {}},
    }
    result = create_automation(plan, approve=True, manifest=manifest, state=state)
    assert result["status"] == "failed"
    assert "invalid schedule" in result["summary"]
    assert state.list() == []
