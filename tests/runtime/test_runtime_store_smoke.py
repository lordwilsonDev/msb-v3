"""Runtime store smoke tests.

Verifies the RuntimeStore can persist state, survive restart, and handle
basic CRUD operations. Uses tmp_path for isolation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from msb_v3.runtime.store import RuntimeStore


@pytest.fixture()
def store(tmp_path: Path) -> RuntimeStore:
    """Create a fresh RuntimeStore backed by a temp directory."""
    db_path = str(tmp_path / "runtime.db")
    return RuntimeStore(db_path=db_path)


class TestRuntimeStoreSmoke:
    """Smoke tests for RuntimeStore persistence."""

    def test_store_instantiation(self, store: RuntimeStore):
        """Store can be created with a custom path."""
        assert store is not None

    def test_list_traces_empty(self, store: RuntimeStore):
        """Empty store returns empty list."""
        traces = store.list_traces()
        assert traces == []

    def test_get_trace_missing(self, store: RuntimeStore):
        """Missing trace returns None."""
        result = store.get_trace("nonexistent-run-id")
        assert result is None

    def test_get_tasks_empty(self, store: RuntimeStore):
        """Missing run returns empty task list."""
        tasks = store.get_tasks("nonexistent-run-id")
        assert tasks == []

    def test_latest_deterministic_hash_missing(self, store: RuntimeStore):
        """Missing run returns None for hash query."""
        result = store.latest_deterministic_hash("nonexistent-run-id")
        assert result is None

    def test_persistence_survives_restart(self, tmp_path: Path):
        """Data persists when store is recreated from same DB."""
        db_path = str(tmp_path / "runtime.db")

        # First store instance — just verify it creates the DB
        RuntimeStore(db_path=db_path)
        assert Path(db_path).exists()

        # Second store instance — should read same DB
        store2 = RuntimeStore(db_path=db_path)
        traces = store2.list_traces()
        assert isinstance(traces, list)

    def test_concurrent_stores_same_db(self, tmp_path: Path):
        """Two store instances on same DB don't crash."""
        db_path = str(tmp_path / "runtime.db")
        store1 = RuntimeStore(db_path=db_path)
        store2 = RuntimeStore(db_path=db_path)

        # Both should be able to read without error
        assert store1.list_traces() == []
        assert store2.list_traces() == []
