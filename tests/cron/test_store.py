"""Tests for the cron SQLite store (cron/store.py)."""

from __future__ import annotations

import pytest

from msb_v3.cron.store import CronStore


@pytest.fixture()
def store(tmp_path) -> CronStore:
    return CronStore(db_path=str(tmp_path / "cron.db"))


def test_create_and_get_job(store: CronStore) -> None:
    job = store.create_job(
        "daily-backup",
        "Backup Evidence Spine",
        "0 2 * * *",
        {"type": "backup_spine", "params": {"keep": 7}},
    )
    assert job["job_id"] == "daily-backup"
    assert job["enabled"] is True
    assert job["governance"]["max_retries"] == 2
    got = store.get_job("daily-backup")
    assert got["action"]["type"] == "backup_spine"
    assert got["action"]["params"] == {"keep": 7}


def test_create_rejects_bad_schedule(store: CronStore) -> None:
    with pytest.raises(ValueError):
        store.create_job("bad", "Bad", "not a cron", {"type": "health_check"})


def test_create_rejects_duplicate(store: CronStore) -> None:
    store.create_job("dup", "Dup", "* * * * *", {"type": "health_check"})
    with pytest.raises(ValueError, match="already exists"):
        store.create_job("dup", "Dup", "* * * * *", {"type": "health_check"})


def test_get_unknown_raises(store: CronStore) -> None:
    with pytest.raises(KeyError):
        store.get_job("nope")


def test_list_jobs(store: CronStore) -> None:
    store.create_job("a", "A", "* * * * *", {"type": "health_check"})
    store.create_job("b", "B", "0 2 * * *", {"type": "metric_export"}, enabled=False)
    jobs = store.list_jobs()
    assert [j["job_id"] for j in jobs] == ["a", "b"]
    assert jobs[1]["enabled"] is False


def test_update_job(store: CronStore) -> None:
    store.create_job("j", "J", "0 2 * * *", {"type": "health_check"})
    updated = store.update_job("j", enabled=False, schedule="*/5 * * * *")
    assert updated["enabled"] is False
    assert updated["schedule"] == "*/5 * * * *"
    with pytest.raises(ValueError):
        store.update_job("j", schedule="garbage")


def test_delete_job(store: CronStore) -> None:
    store.create_job("j", "J", "* * * * *", {"type": "health_check"})
    store.delete_job("j")
    with pytest.raises(KeyError):
        store.get_job("j")
    with pytest.raises(KeyError):
        store.delete_job("j")


def test_run_lifecycle_and_history(store: CronStore) -> None:
    store.create_job("j", "J", "* * * * *", {"type": "health_check"})
    run_id = store.start_run("j", "manual", attempt=1)
    assert store.is_running("j") is True
    store.finish_run(run_id, "SUCCESS", summary={"ok": True, "summary": "ok", "detail": {}})
    assert store.is_running("j") is False
    hist = store.history("j")
    assert len(hist) == 1
    assert hist[0]["status"] == "SUCCESS"
    assert hist[0]["trigger"] == "manual"
    assert hist[0]["duration_ms"] is not None
    assert hist[0]["summary"]["ok"] is True


def test_finish_run_rejects_bad_status(store: CronStore) -> None:
    store.create_job("j", "J", "* * * * *", {"type": "health_check"})
    run_id = store.start_run("j", "manual")
    with pytest.raises(ValueError):
        store.finish_run(run_id, "BOGUS")


def test_recover_inflight(store: CronStore) -> None:
    store.create_job("j", "J", "* * * * *", {"type": "health_check"})
    run_id = store.start_run("j", "schedule")
    recovered = store.recover_inflight()
    assert len(recovered) == 1
    assert recovered[0]["run_id"] == run_id
    assert store.history("j")[0]["status"] == "INTERRUPTED"


def test_prune_history(store: CronStore) -> None:
    store.create_job("j", "J", "* * * * *", {"type": "health_check"})
    for _ in range(5):
        rid = store.start_run("j", "manual")
        store.finish_run(rid, "SUCCESS", summary={"ok": True, "summary": "ok", "detail": {}})
    removed = store.prune_history("j", keep=2)
    assert removed == 3
    assert len(store.history("j")) == 2
