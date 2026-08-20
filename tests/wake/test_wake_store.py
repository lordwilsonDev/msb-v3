"""Tests for the wake inbox/outbox store (wake/store.py)."""

from __future__ import annotations

import pytest

from msb_v3.wake.store import WakeStore


def test_post_and_pending(tmp_path) -> None:
    store = WakeStore(db_path=str(tmp_path / "wake.db"))
    assert store.pending_count() == 0
    row = store.post("hello resident", sender="other-session")
    assert row["status"] == "pending"
    assert row["sender"] == "other-session"
    assert store.pending_count() == 1
    pending = store.pending()
    assert len(pending) == 1
    assert pending[0]["id"] == row["id"]


def test_post_requires_text(tmp_path) -> None:
    store = WakeStore(db_path=str(tmp_path / "wake.db"))
    with pytest.raises(ValueError):
        store.post("   ")
    with pytest.raises(ValueError):
        store.post("")


def test_respond_marks_done_and_writes_outbox(tmp_path) -> None:
    store = WakeStore(db_path=str(tmp_path / "wake.db"))
    msg = store.post("wake up")
    out = store.respond(msg["id"], "I'm awake")
    assert out["in_reply_to"] == msg["id"]
    assert store.pending_count() == 0
    assert store.get_inbox(msg["id"])["status"] == "done"
    outbox = store.outbox()
    assert len(outbox) == 1
    assert outbox[0]["text"] == "I'm awake"


def test_mark_failed_keeps_error(tmp_path) -> None:
    store = WakeStore(db_path=str(tmp_path / "wake.db"))
    msg = store.post("do the thing")
    store.mark_failed(msg["id"], "boom")
    row = store.get_inbox(msg["id"])
    assert row["status"] == "failed"
    assert row["error"] == "boom"
    # Failed messages are never picked up again.
    assert store.pending_count() == 0


def test_respond_unknown_message_raises(tmp_path) -> None:
    store = WakeStore(db_path=str(tmp_path / "wake.db"))
    with pytest.raises(KeyError):
        store.respond("wake-nope", "hi")
    with pytest.raises(KeyError):
        store.get_inbox("wake-nope")


def test_pending_order_and_limit(tmp_path) -> None:
    store = WakeStore(db_path=str(tmp_path / "wake.db"))
    for i in range(3):
        store.post(f"msg {i}")
    assert [m["text"] for m in store.pending(limit=2)] == ["msg 0", "msg 1"]
    assert len(store.pending(limit=10)) == 3
