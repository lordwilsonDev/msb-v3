"""Tests for memory store."""

from __future__ import annotations

import pytest

from msb_v3.memory.store import MemoryStore, Message


@pytest.fixture()
def tmp_store(tmp_path):
    return MemoryStore(str(tmp_path / "test.db"))


def test_append_and_recent(tmp_store):
    tmp_store.append("s1", Message("user", "hello"))
    tmp_store.append("s1", Message("assistant", "hi"))
    msgs = tmp_store.recent("s1", limit=2)
    assert len(msgs) == 2
    assert msgs[0].role == "assistant"
    assert msgs[0].content == "hi"


def test_clear(tmp_store):
    tmp_store.append("s1", Message("user", "hello"))
    tmp_store.clear("s1")
    assert tmp_store.recent("s1") == []
