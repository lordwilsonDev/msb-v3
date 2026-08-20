"""Tests for the living-automation state store (automation/state.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from msb_v3.automation.state import AutomationState


def test_upsert_and_get(tmp_path) -> None:
    state = AutomationState(db_path=str(tmp_path / "state.db"))
    row = state.upsert("auto-1", "*/5 * * * *", enabled=True)
    assert row["automation_id"] == "auto-1"
    assert row["enabled"] is True
    assert row["schedule"] == "*/5 * * * *"
    assert row["next_run"] is not None  # computed from the schedule


def test_set_enabled_flips_contract(tmp_path) -> None:
    state = AutomationState(db_path=str(tmp_path / "state.db"))
    state.upsert("auto-1", "*/5 * * * *")
    assert state.set_enabled("auto-1", False)["enabled"] is False
    assert state.get("auto-1")["enabled"] is False
    # Disabled automations are never due.
    assert state.due() == []


def test_mark_run_advances_next_run(tmp_path) -> None:
    state = AutomationState(db_path=str(tmp_path / "state.db"))
    state.upsert("auto-1", "* * * * *")
    state.mark_run("auto-1", "SUCCESS", "webhook_post ok")
    after = state.get("auto-1")
    assert after["last_run_status"] == "SUCCESS"
    assert after["last_run_summary"] == "webhook_post ok"
    assert after["next_run"] is not None
    # The next scheduled run is never before the run that just completed.
    assert after["next_run"] >= after["last_run_ts"]


def test_due_respects_next_run(tmp_path) -> None:
    state = AutomationState(db_path=str(tmp_path / "state.db"))
    state.upsert("auto-hourly", "0 * * * *")
    # next_run is the next hour boundary — now is far before it, so nothing due.
    assert state.due(now=datetime.now(timezone.utc)) == []
    # After a run, next_run advances to the following boundary; a now far
    # past it makes the automation due again.
    state.mark_run("auto-hourly", "SUCCESS", "x")
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    due = state.due(now=future)
    assert [r["automation_id"] for r in due] == ["auto-hourly"]


def test_unknown_automation_raises(tmp_path) -> None:
    state = AutomationState(db_path=str(tmp_path / "state.db"))
    try:
        state.get("nope")
        assert False, "expected KeyError"
    except KeyError:
        pass
