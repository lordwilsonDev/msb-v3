"""Tests for the wake cycle runner (wake/runner.py).

The cycle must be bounded, must never abort on one bad message, and must
route automation requests to the brain (which dry-runs by default — no
side effects in tests).
"""

from __future__ import annotations

from msb_v3.automation.brain import try_parse_plan
from msb_v3.wake.runner import ensure_wake_job, run_wake_cycle
from msb_v3.wake.store import WakeStore


def _noop_dispatcher() -> dict:
    return {"ok": True, "summary": "dispatcher: nothing due", "detail": {"ran": [], "failed": []}}


def _noop_audit() -> dict:
    return {"ok": True, "summary": "audit: 0 finding(s), unchanged", "findings": [], "changed": False}


def _cycle(store, **kw):
    """Run the cycle hermetic: stub the dispatcher + audit legs unless the
    test explicitly passes its own."""
    kw.setdefault("dispatcher_fn", _noop_dispatcher)
    kw.setdefault("audit_fn", _noop_audit)
    return run_wake_cycle(store=store, **kw)


def test_empty_inbox_is_a_noop(tmp_path) -> None:
    store = WakeStore(db_path=str(tmp_path / "wake.db"))
    result = _cycle(store, turn_fn=lambda text, sender: "hi")
    assert result["ok"] is True
    assert "inbox empty" in result["summary"]
    assert result["detail"]["pending_remaining"] == 0
    assert result["detail"]["dispatch"] == {"ran": [], "failed": []}
    assert result["detail"]["audit"] == []


def test_cycle_processes_and_responds(tmp_path) -> None:
    store = WakeStore(db_path=str(tmp_path / "wake.db"))
    store.post("wake up", sender="session-a")
    store.post("second", sender="session-b")

    def turn(text: str, sender: str) -> str:
        return f"got: {text} (from {sender})"

    result = _cycle(store, turn_fn=turn)
    assert len(result["detail"]["processed"]) == 2
    assert result["detail"]["failed"] == []
    assert store.pending_count() == 0
    replies = {o["in_reply_to"]: o["text"] for o in store.outbox()}
    assert len(replies) == 2
    assert "got: wake up (from session-a)" in replies.values()


def test_cycle_bounded_by_max_items(tmp_path) -> None:
    store = WakeStore(db_path=str(tmp_path / "wake.db"))
    for i in range(5):
        store.post(f"msg {i}")
    result = _cycle(store, max_items=2, turn_fn=lambda text, sender: "ok")
    assert len(result["detail"]["processed"]) == 2
    assert len(result["detail"]["processed"]) == 2  # bounded, not all 5
    assert store.pending_count() == 3


def test_one_bad_turn_does_not_abort_cycle(tmp_path) -> None:
    store = WakeStore(db_path=str(tmp_path / "wake.db"))
    store.post("first")
    store.post("boom")

    def turn(text: str, sender: str) -> str:
        if text == "boom":
            raise RuntimeError("model exploded")
        return f"handled {text}"

    result = _cycle(store, turn_fn=turn)
    assert len(result["detail"]["processed"]) == 1
    assert len(result["detail"]["failed"]) == 1
    assert result["detail"]["failed"][0]["error"].startswith("RuntimeError")
    # The failed message stays visible with its error, never silently dropped.
    row = store.get_inbox(result["detail"]["failed"][0]["id"])
    assert row["status"] == "failed"
    assert row["error"] == "RuntimeError: model exploded"


def test_automation_hook_routes_plan_to_brain_dry_run(tmp_path, monkeypatch) -> None:
    """A wake turn that emits a fenced automation plan triggers the brain,
    which dry-runs by default — the outbox reply carries the dry_run note
    and nothing is created."""
    store = WakeStore(db_path=str(tmp_path / "wake.db"))
    store.post("build me an n8n workflow that replies to webhooks")

    plan_block = '{"automation": {"provider": "n8n", "name": "webhook echo", "description": "echo webhook payloads"}}'

    def turn(text: str, sender: str) -> str:
        return f"on it. ```json\n{plan_block}\n```"

    result = _cycle(store, turn_fn=turn)
    assert result["ok"] is True
    replies = store.outbox()
    assert len(replies) == 1
    assert "[automation] dry_run" in replies[0]["text"]
    assert "approve" in replies[0]["text"]


def test_try_parse_plan_handles_malformed(tmp_path) -> None:
    assert try_parse_plan("no plan here") is None
    assert try_parse_plan("```json\nnot json\n```") is None
    assert try_parse_plan("```json\n{\"automation\": {\"provider\": \"n8n\"}}\n```") is None  # missing name/description
    plan = try_parse_plan('```json\n{"automation": {"provider": "ghl", "name": "x", "description": "y"}}\n```')
    assert plan == {"provider": "ghl", "name": "x", "description": "y"}


def test_ensure_wake_job_seeds_once(tmp_path, monkeypatch) -> None:
    from msb_v3.cron.store import CronStore

    cron_store = CronStore(db_path=str(tmp_path / "cron.db"))
    assert ensure_wake_job(cron_store) is True
    job = cron_store.get_job("wake-agent")
    assert job["action"]["type"] == "wake_agent"
    assert job["schedule"] == "*/5 * * * *"
    # Idempotent — second call does not clobber.
    assert ensure_wake_job(cron_store) is True
    assert len(cron_store.list_jobs()) == 1


def test_cycle_ticks_dispatcher_and_audit(monkeypatch, tmp_path) -> None:
    """The wake cycle is the clock for everything: it runs the dispatcher
    (due living automations) and the audit on every pass, even with an empty
    inbox — that is the self-maintenance contract."""
    store = WakeStore(db_path=str(tmp_path / "wake.db"))

    dispatched: list[str] = []

    def fake_dispatcher() -> dict:
        dispatched.append("tick")
        return {"ok": True, "summary": "dispatcher: 1 ran, 0 failed", "detail": {"ran": ["auto-abc"], "failed": []}}

    def fake_audit() -> dict:
        return {"ok": True, "summary": "audit: 2 finding(s), changed", "findings": [{"kind": "provider"}], "changed": True}

    result = run_wake_cycle(store=store, turn_fn=lambda t, s: "hi", dispatcher_fn=fake_dispatcher, audit_fn=fake_audit)
    assert dispatched == ["tick"]
    assert result["detail"]["dispatch"] == {"ran": ["auto-abc"], "failed": []}
    assert result["detail"]["audit"] == [{"kind": "provider"}]
    assert result["detail"]["audit_changed"] is True
    assert "dispatcher: 1 ran" in result["summary"]
    assert "audit: 2 finding(s)" in result["summary"]
