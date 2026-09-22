"""Tests for the /ops/background snapshot (ops/background.py).

Classifiers are pure (raw dict + now -> Entry), so most tests need no DB.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from msb_v3.ops.background import (
    FAIL,
    OK,
    UNKNOWN,
    WARN,
    Entry,
    classify_cron,
    missed_fire,
    parse_ts,
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
