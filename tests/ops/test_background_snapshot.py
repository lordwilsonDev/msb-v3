"""Tests for the /ops/background snapshot (ops/background.py).

Classifiers are pure (raw dict + now -> Entry), so most tests need no DB.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from msb_v3.ops.background import (
    FAIL,
    OK,
    UNKNOWN,
    WARN,
    Entry,
    classify_automation,
    classify_cron,
    classify_governance,
    classify_plei,
    classify_wake,
    missed_fire,
    parse_ts,
    READERS,
    SnapshotCache,
    build_snapshot,
    read_cron,
    read_plei,
    read_wake,
)

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _job(job_id="j", schedule="*/5 * * * *", enabled=True, status="SUCCESS", started=NOW - timedelta(minutes=2)):
    last = None if status is None else {"status": status, "started_at": started.isoformat()}
    return {"job_id": job_id, "schedule": schedule, "enabled": enabled, "last_run": last}


# --- helpers -------------------------------------------------------------
def test_parse_ts_handles_z_naive_and_garbage():
    assert parse_ts("2026-09-22T12:00:00Z") == NOW
    assert parse_ts("2026-09-22T12:00:00") == NOW  # naive -> UTC
    assert parse_ts("not a time") is None
    assert parse_ts(None) is None
    assert parse_ts("") is None


def test_missed_fire_true_only_when_a_due_fire_plus_grace_has_passed():
    grace = timedelta(minutes=2)
    # every 5 min; last ran 12 min ago -> the fire 7 min ago was missed
    assert missed_fire("*/5 * * * *", NOW - timedelta(minutes=12), NOW, grace) is True
    # last ran 2 min ago -> next fire is in the future
    assert missed_fire("*/5 * * * *", NOW - timedelta(minutes=2), NOW, grace) is False
    # the 11:55 fire was missed, but a 6 min grace still covers it
    assert missed_fire("*/5 * * * *", NOW - timedelta(minutes=6), NOW, timedelta(minutes=6)) is False
    # same fire with the 2 min grace -> missed
    assert missed_fire("*/5 * * * *", NOW - timedelta(minutes=6), NOW, grace) is True


def test_entry_as_dict():
    assert Entry(OK, {"a": 1}).as_dict() == {"state": "ok", "detail": {"a": 1}, "error": None}


# --- cron ----------------------------------------------------------------
def test_cron_ok_when_enabled_and_recent_success():
    e = classify_cron({"enabled": True, "tick_s": 30, "jobs": [_job()]}, NOW)
    assert e.state == OK
    assert e.detail == {"scheduler_enabled": True, "job_count": 1, "failed": [], "overdue": []}


def test_cron_fail_when_latest_run_of_enabled_job_failed():
    e = classify_cron({"enabled": True, "tick_s": 30, "jobs": [_job(status="FAILED")]}, NOW)
    assert e.state == FAIL
    assert e.detail["failed"] == ["j"]


def test_cron_failed_disabled_job_is_ignored():
    e = classify_cron({"enabled": True, "tick_s": 30, "jobs": [_job(enabled=False, status="FAILED")]}, NOW)
    assert e.state == OK


def test_cron_warn_when_overdue():
    job = _job(started=NOW - timedelta(minutes=30))
    e = classify_cron({"enabled": True, "tick_s": 30, "jobs": [job]}, NOW)
    assert e.state == WARN
    assert e.detail["overdue"] == ["j"]


def test_cron_never_run_job_is_not_overdue():
    e = classify_cron({"enabled": True, "tick_s": 30, "jobs": [_job(status=None)]}, NOW)
    assert e.state == OK


def test_cron_warn_when_scheduler_disabled():
    e = classify_cron({"enabled": False, "tick_s": 30, "jobs": [_job()]}, NOW)
    assert e.state == WARN
    assert e.detail["scheduler_enabled"] is False


def test_cron_bad_schedule_does_not_blank_the_entry():
    e = classify_cron({"enabled": True, "tick_s": 30, "jobs": [_job(schedule="nonsense")]}, NOW)
    assert e.state == OK
    assert e.detail["overdue"] == []


# --- governance ------------------------------------------------------------
def _gov(armed=False, scopes=None, budgets=None, pending=0, **ks):
    return {
        "killswitch": {"armed": armed, "scopes": scopes or [], **ks},
        "budgets": budgets or {"llm": {"spent": 1, "limit": 100}},
        "approvals": {"pending": pending},
    }


def test_governance_ok():
    e = classify_governance(_gov(pending=2), NOW)
    assert e.state == OK
    assert e.detail["pending_approvals"] == 2
    assert e.detail["killswitch_armed"] is False


def test_governance_fail_when_killswitch_armed():
    assert classify_governance(_gov(armed=True), NOW).state == FAIL


def test_governance_fail_closed_state_is_fail():
    e = classify_governance(_gov(armed=True, fail_closed=True), NOW)
    assert e.state == FAIL
    assert e.detail["fail_closed"] is True


def test_governance_warn_on_armed_scope():
    e = classify_governance(_gov(scopes=[{"scope_type": "tool", "scope_id": "vault_write"}]), NOW)
    assert e.state == WARN
    assert e.detail["armed_scopes"] == 1


def test_governance_warn_when_scope_list_unreadable():
    e = classify_governance(_gov(scopes=[{"error": "db locked"}]), NOW)
    assert e.state == WARN
    assert e.detail["scope_read_error"] is True


def test_governance_warn_when_budget_over_80pct_and_unlimited_ignored():
    budgets = {"llm": {"spent": 81, "limit": 100}, "tools": {"spent": 999, "limit": -1}}
    e = classify_governance(_gov(budgets=budgets), NOW)
    assert e.state == WARN
    assert e.detail["budgets_over_80pct"] == ["llm"]


def test_governance_unknown_without_killswitch_block():
    assert classify_governance({"budgets": {}}, NOW).state == UNKNOWN


# --- automation ------------------------------------------------------------
def _auto(spent=1.0, cap=10.0, dry_run=True):
    return {
        "dry_run": dry_run,
        "budget": {"cap_usd": cap, "spent_usd": spent, "remaining_usd": cap - spent},
        "providers": {"n8n": {"configured": True}, "make": {"configured": False}},
    }


def test_automation_ok_with_detail():
    e = classify_automation(_auto(), NOW)
    assert e.state == OK
    assert e.detail["dry_run"] is True
    assert e.detail["providers_configured"] == 1
    assert e.detail["providers_total"] == 2


def test_automation_warn_over_80pct_of_cap():
    assert classify_automation(_auto(spent=8.5, cap=10.0), NOW).state == WARN


def test_automation_zero_cap_is_not_a_warning():
    assert classify_automation(_auto(spent=0.0, cap=0.0), NOW).state == OK


# --- wake ------------------------------------------------------------------
def _wake(enabled=True, pending=0, job_present=True, oldest=None):
    return {
        "enabled": enabled,
        "schedule": "*/5 * * * *",
        "pending": pending,
        "outbox_count": 3,
        "job_present": job_present,
        "oldest_pending_ts": oldest,
    }


def test_wake_ok_when_idle():
    e = classify_wake(_wake(), NOW)
    assert e.state == OK
    assert e.detail["outbox_count"] == 3


def test_wake_fail_when_enabled_but_no_cron_job():
    assert classify_wake(_wake(job_present=False), NOW).state == FAIL


def test_wake_disabled_is_ok_not_fail():
    assert classify_wake(_wake(enabled=False, job_present=False), NOW).state == OK


def test_wake_warn_when_oldest_pending_missed_a_cycle():
    old = (NOW - timedelta(minutes=20)).isoformat()
    assert classify_wake(_wake(pending=1, oldest=old), NOW).state == WARN


def test_wake_recent_pending_is_ok():
    recent = (NOW - timedelta(minutes=1)).isoformat()
    assert classify_wake(_wake(pending=1, oldest=recent), NOW).state == OK


# --- plei --------------------------------------------------------------------
def _plei(chain_ok=True):
    return {
        "predictions": 4,
        "outcomes": 2,
        "pairs": 2,
        "chain_ok": chain_ok,
        "chain_message": "ok" if chain_ok else "hash mismatch at 3",
        "last_forecast_at": "2026-09-22T11:00:00Z",
    }


def test_plei_ok():
    e = classify_plei(_plei(), NOW)
    assert e.state == OK
    assert e.detail["pairs"] == 2


def test_plei_fail_on_broken_chain():
    assert classify_plei(_plei(chain_ok=False), NOW).state == FAIL


# --- readers (real stores; conftest redirects their DBs to tmp_path) --------
def test_read_cron_reports_jobs_with_last_run():
    from msb_v3.cron.store import CronStore

    store = CronStore()
    store.create_job("hb", "Heartbeat", "*/5 * * * *", {"type": "health_check", "params": {}})
    run_id = store.start_run("hb", "manual")
    store.finish_run(run_id, "SUCCESS")
    raw = asyncio.run(read_cron())
    assert [j["job_id"] for j in raw["jobs"]] == ["hb"]
    assert raw["jobs"][0]["last_run"]["status"] == "SUCCESS"
    assert "tick_s" in raw and "enabled" in raw


def test_read_wake_adds_oldest_pending_ts():
    from msb_v3.wake.store import WakeStore

    WakeStore().post("hello", sender="test")
    raw = asyncio.run(read_wake())
    assert raw["pending"] == 1
    assert raw["oldest_pending_ts"]


def test_read_plei_from_explicit_store(tmp_path):
    from msb_v3.plei.calibration.store import CalibrationStore

    raw = asyncio.run(read_plei(store=CalibrationStore(path=tmp_path / "cal.jsonl")))
    assert raw == {
        "predictions": 0,
        "outcomes": 0,
        "pairs": 0,
        "chain_ok": True,
        "chain_message": "empty chain",
        "last_forecast_at": None,
    }


# --- snapshot ----------------------------------------------------------------
def test_readers_cover_the_five_subsystems_in_order():
    assert list(READERS) == ["cron", "governance", "automation", "wake", "plei"]


def test_build_snapshot_isolates_a_failing_reader():
    async def good():
        return {"predictions": 1, "outcomes": 0, "pairs": 0, "chain_ok": True,
                "chain_message": "ok", "last_forecast_at": None}

    async def broken():
        raise RuntimeError("db locked")

    snap = asyncio.run(build_snapshot(
        readers={"plei": (good, classify_plei), "cron": (broken, classify_cron)}, now=NOW,
    ))
    assert snap["generated_at"] == NOW.isoformat()
    assert snap["subsystems"]["plei"]["state"] == OK
    assert snap["subsystems"]["cron"] == {"state": UNKNOWN, "detail": {}, "error": "RuntimeError: db locked"}


def test_build_snapshot_isolates_a_failing_classifier():
    async def read():
        # Enabled job whose latest run FAILED but with no job_id: classify_cron's
        # job["job_id"] raises KeyError -> must surface as unknown. (The plan's
        # original {"jobs": [{"job_id": "x"}]} never raises — .get()-safe code
        # skips an unenabled job — so it produced warn, not unknown.)
        return {"enabled": True, "jobs": [{"enabled": True, "last_run": {"status": "FAILED"}}]}

    snap = asyncio.run(build_snapshot(readers={"cron": (read, classify_cron)}, now=NOW))
    assert snap["subsystems"]["cron"]["state"] == UNKNOWN


def test_cache_reuses_within_ttl_and_rebuilds_after():
    t = [100.0]
    calls = []

    async def build():
        calls.append(t[0])
        return {"n": len(calls)}

    cache = SnapshotCache(ttl_s=3.0, clock=lambda: t[0])
    assert asyncio.run(cache.get(build)) == {"n": 1}
    t[0] = 102.0
    assert asyncio.run(cache.get(build)) == {"n": 1}
    t[0] = 103.5
    assert asyncio.run(cache.get(build)) == {"n": 2}
