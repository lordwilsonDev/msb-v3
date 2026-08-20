"""Tests for the dispatcher (automation/dispatcher.py) — executes due
living automations from the state store + manifest, fail-closed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx

from msb_v3.automation.dispatcher import run_due
from msb_v3.automation.manifest import Manifest
from msb_v3.automation.state import AutomationState


def _register(state: AutomationState, manifest: Manifest, schedule: str, action: dict) -> str:
    entry = manifest.append(
        provider="self",
        name="heartbeat",
        description="post a heartbeat",
        status="created",
        summary="registered",
        schedule=schedule,
        action=action,
    )
    state.upsert(entry["id"], schedule, enabled=True)
    return entry["id"]


def test_nothing_due_is_a_noop(tmp_path) -> None:
    state = AutomationState(db_path=str(tmp_path / "state.db"))
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    result = run_due(state=state, manifest=manifest)
    assert result["ok"] is True
    assert result["detail"] == {"ran": [], "failed": []}


def test_due_automation_posts_and_records_success(tmp_path) -> None:
    state = AutomationState(db_path=str(tmp_path / "state.db"))
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    auto_id = _register(
        state,
        manifest,
        "* * * * *",
        {"type": "webhook_post", "url": "http://127.0.0.1:9/hook/msb-ping", "payload": {"$now": ""}},
    )
    transport = httpx.MockTransport(handler)
    now = datetime.now(timezone.utc) + timedelta(minutes=5)  # past next_run
    result = run_due(state=state, manifest=manifest, now=now, transport=transport)
    assert result["detail"]["ran"] == [auto_id]
    assert result["detail"]["failed"] == []
    assert len(calls) == 1
    assert "$now" in calls[0]  # the $now placeholder was materialized
    row = state.get(auto_id)
    assert row["last_run_status"] == "SUCCESS"


def test_allowlist_refuses_unknown_host(tmp_path) -> None:
    state = AutomationState(db_path=str(tmp_path / "state.db"))
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    auto_id = _register(
        state,
        manifest,
        "* * * * *",
        {"type": "webhook_post", "url": "https://evil.example.com/steal", "payload": {}},
    )
    result = run_due(state=state, manifest=manifest, now=datetime.now(timezone.utc) + timedelta(minutes=5))
    assert result["detail"]["ran"] == []
    assert result["detail"]["failed"][0]["id"] == auto_id
    assert "allowlist" in result["detail"]["failed"][0]["error"]
    assert state.get(auto_id)["last_run_status"] == "FAILED"


def test_unknown_action_type_fails_closed(tmp_path) -> None:
    state = AutomationState(db_path=str(tmp_path / "state.db"))
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    auto_id = _register(
        state,
        manifest,
        "* * * * *",
        {"type": "rm_rf", "url": "http://127.0.0.1:9/", "payload": {}},
    )
    result = run_due(state=state, manifest=manifest, now=datetime.now(timezone.utc) + timedelta(minutes=5))
    assert result["detail"]["failed"][0]["id"] == auto_id
    assert "unknown automation action type" in result["detail"]["failed"][0]["error"]


def test_missing_manifest_entry_is_a_failed_run(tmp_path) -> None:
    state = AutomationState(db_path=str(tmp_path / "state.db"))
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    state.upsert("auto-ghost", "* * * * *")
    result = run_due(state=state, manifest=manifest, now=datetime.now(timezone.utc) + timedelta(minutes=5))
    assert result["detail"]["failed"][0]["id"] == "auto-ghost"
    assert result["detail"]["failed"][0]["error"] == "manifest entry missing"
