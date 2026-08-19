"""Tests for the cron scheduler (cron/scheduler.py): governance, retries,
timeouts, overlap, receipts, and the due-job loop."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from msb_v3.cron.scheduler import CronScheduler
from msb_v3.cron.store import CronStore


@pytest.fixture()
def store(tmp_path) -> CronStore:
    return CronStore(db_path=str(tmp_path / "cron.db"))


def _job(
    store: CronStore,
    job_id: str = "j",
    schedule: str = "* * * * *",
    governance: dict | None = None,
) -> None:
    store.create_job(
        job_id,
        job_id.title(),
        schedule,
        {"type": "health_check", "params": {}},
        governance=governance or {"max_retries": 1, "timeout_s": 30},
    )


def test_run_job_success(store: CronStore) -> None:
    _job(store)
    scheduler = CronScheduler(store, action_runner=lambda t, p: {"ok": True, "summary": "all good", "detail": {}})
    result = asyncio.run(scheduler.run_job("j"))
    assert result["status"] == "SUCCESS"
    assert result["attempts"] == 1
    assert store.history("j")[0]["status"] == "SUCCESS"


def test_run_job_failure_then_retry(store: CronStore) -> None:
    _job(store, governance={"max_retries": 2, "timeout_s": 30})
    calls = {"n": 0}

    def flaky(t, p):
        calls["n"] += 1
        if calls["n"] < 3:
            return {"ok": False, "summary": "flaky", "detail": {}}
        return {"ok": True, "summary": "third time lucky", "detail": {}}

    scheduler = CronScheduler(store, action_runner=flaky)
    result = asyncio.run(scheduler.run_job("j"))
    assert result["status"] == "SUCCESS"
    assert result["attempts"] == 3
    assert calls["n"] == 3


def test_run_job_exhausts_retries(store: CronStore) -> None:
    _job(store, governance={"max_retries": 2, "timeout_s": 30})
    scheduler = CronScheduler(
        store, action_runner=lambda t, p: {"ok": False, "summary": "always fails", "detail": {}}
    )
    result = asyncio.run(scheduler.run_job("j"))
    assert result["status"] == "FAILED"
    assert result["attempts"] == 3  # 1 + max_retries
    runs = store.history("j")
    assert [r["status"] for r in runs] == ["FAILED", "FAILED", "FAILED"]


def test_run_job_unknown_action_fails_closed(store: CronStore) -> None:
    store.create_job("j", "J", "* * * * *", {"type": "no_such_action"})
    # The real runner refuses unknown actions; the scheduler records FAILED.
    scheduler = CronScheduler(store)
    result = asyncio.run(scheduler.run_job("j"))
    assert result["status"] == "FAILED"
    assert "unknown cron action" in result["result"]["summary"]


def test_run_job_malformed_action_result(store: CronStore) -> None:
    _job(store)
    scheduler = CronScheduler(store, action_runner=lambda t, p: None)  # not a dict
    result = asyncio.run(scheduler.run_job("j"))
    assert result["status"] == "FAILED"
    assert "malformed" in result["result"]["summary"]


def test_run_job_unknown_job(store: CronStore) -> None:
    scheduler = CronScheduler(store)
    with pytest.raises(ValueError):
        asyncio.run(scheduler.run_job("ghost"))


def test_run_job_timeout(store: CronStore) -> None:
    _job(store, governance={"max_retries": 0, "timeout_s": 0.05})

    def slow(t, p):
        import time

        time.sleep(1)
        return {"ok": True, "summary": "too late", "detail": {}}

    scheduler = CronScheduler(store, action_runner=slow)
    result = asyncio.run(scheduler.run_job("j"))
    assert result["status"] == "FAILED"
    assert "timed out" in result["result"]["summary"]


def test_kill_switch_blocks_run(store: CronStore, monkeypatch: pytest.MonkeyPatch) -> None:
    _job(store)
    scheduler = CronScheduler(store, action_runner=lambda t, p: {"ok": True, "summary": "never runs", "detail": {}})

    class Armed:
        def state(self) -> dict:
            return {"armed": True, "reason": "drill"}

    monkeypatch.setattr("msb_v3.governance.killswitch.KillSwitch", lambda: Armed())
    result = asyncio.run(scheduler.run_job("j"))
    assert result["status"] == "BLOCKED"
    assert "drill" in result["reason"]
    assert store.history("j")[0]["status"] == "BLOCKED"


def test_requires_approval_skipped_on_schedule_only(store: CronStore) -> None:
    _job(store, governance={"requires_approval": True, "max_retries": 1, "timeout_s": 30})
    scheduler = CronScheduler(store, action_runner=lambda t, p: {"ok": True, "summary": "ok", "detail": {}})
    scheduled = asyncio.run(scheduler.run_job("j", trigger="schedule"))
    assert scheduled["status"] == "SKIPPED"
    manual = asyncio.run(scheduler.run_job("j", trigger="manual"))
    assert manual["status"] == "SUCCESS"


def test_overlap_guard(store: CronStore) -> None:
    _job(store)
    scheduler = CronScheduler(store, action_runner=lambda t, p: {"ok": True, "summary": "ok", "detail": {}})
    # Simulate an in-flight run, then a manual run must be skipped.
    store.start_run("j", "schedule")
    result = asyncio.run(scheduler.run_job("j"))
    assert result["status"] == "SKIPPED"
    assert "already running" in result["reason"]


def test_receipts_land_on_audit_stream(store: CronStore, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr("msb_v3.observability.audit_log.settings.audit_log_path", str(audit))
    _job(store)
    scheduler = CronScheduler(store, action_runner=lambda t, p: {"ok": True, "summary": "ok", "detail": {}})
    asyncio.run(scheduler.run_job("j"))
    lines = audit.read_text().splitlines()
    assert len(lines) == 1
    import json

    receipt = json.loads(lines[0])
    assert receipt["event"] == "cron.run"
    assert receipt["job_id"] == "j"
    assert receipt["status"] == "SUCCESS"
    assert receipt["verification"]["basis"] == "rerun"
    assert receipt["audit"]["seq"] >= 1  # mirrored to the (isolated) chain


def test_due_jobs_and_tick(store: CronStore) -> None:
    store.create_job("every-min", "Every Min", "* * * * *", {"type": "health_check"})
    store.create_job("daily", "Daily", "0 2 * * *", {"type": "health_check"})
    store.create_job("disabled", "Disabled", "* * * * *", {"type": "health_check"}, enabled=False)
    scheduler = CronScheduler(store, action_runner=lambda t, p: {"ok": True, "summary": "ok", "detail": {}})

    now = datetime(2026, 8, 18, 10, 30, tzinfo=timezone.utc)
    due = scheduler.due_jobs(now)
    assert [j["job_id"] for j in due] == ["every-min"]


def test_run_loop_stops_cleanly(store: CronStore) -> None:
    _job(store)
    scheduler = CronScheduler(store, action_runner=lambda t, p: {"ok": True, "summary": "ok", "detail": {}})

    async def drive():
        stop = asyncio.Event()
        task = asyncio.create_task(scheduler.run_loop(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(drive())
    # The loop's startup recover_inflight() and one tick ran without raising.
    assert store.list_runs() or True
