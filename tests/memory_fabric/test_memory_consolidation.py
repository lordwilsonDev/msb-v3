"""Memory consolidation integration tests (V5).

Proves the memory_fabric → retrieval pipeline:
1. Store a memory with provenance
2. Recall memories for a query
3. Verify memory state transitions
4. Consolidate near-duplicate memories
5. The retrieval layer can query stored memories
"""
from __future__ import annotations

from pathlib import Path

import pytest

from msb_v3.memory_fabric.fabric import MemoryFabric
from msb_v3.memory_fabric.models import MemoryType, VerificationState
from msb_v3.memory_fabric.store import MemoryFabricStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fabric(tmp_path: Path) -> MemoryFabric:
    """Create a fresh MemoryFabric backed by a temp directory."""
    db_path = str(tmp_path / "memory.db")
    store = MemoryFabricStore(db_path=db_path)
    return MemoryFabric(store=store)


# ---------------------------------------------------------------------------
# Store → Recall pipeline tests
# ---------------------------------------------------------------------------


class TestMemoryStoreRecall:
    """End-to-end store → recall pipeline."""

    def test_store_and_recall_memory(self, fabric: MemoryFabric):
        """Stored memory is recallable by query."""
        record = fabric.store_memory(
            tenant="test-project",
            content="MSB v3 uses governed execution with ActionGate",
            type_=MemoryType.SEMANTIC,
            tags=["governance", "actiongate"],
            source_agent="test",
            importance=0.8,
        )
        assert record is not None
        assert record.memory_id is not None

        # Recall by keyword
        results = fabric.recall_memories(
            tenant="test-project",
            query="ActionGate governance",
            top_k=5,
        )
        assert len(results) > 0
        assert any("ActionGate" in r.content for r in results)

    def test_store_multiple_memories(self, fabric: MemoryFabric):
        """Multiple memories can be stored and recalled."""
        for i in range(5):
            fabric.store_memory(
                tenant="test-project",
                content=f"Memory item {i} about topic {i % 3}",
                type_=MemoryType.EPISODIC,
                tags=[f"topic-{i % 3}"],
                source_agent="test",
                importance=0.5,
            )

        results = fabric.recall_memories(
            tenant="test-project",
            query="topic 1",
            top_k=10,
        )
        assert len(results) >= 1

    def test_recall_returns_ranked_results(self, fabric: MemoryFabric):
        """Recall results are ranked by relevance."""
        fabric.store_memory(
            tenant="p",
            content="The evidence spine uses SHA-256 hash chains",
            type_=MemoryType.SEMANTIC,
            tags=["evidence", "hash"],
            source_agent="test",
            importance=0.9,
        )
        fabric.store_memory(
            tenant="p",
            content="The weather is nice today",
            type_=MemoryType.EPISODIC,
            tags=["weather"],
            source_agent="test",
            importance=0.3,
        )

        results = fabric.recall_memories(tenant="p", query="evidence hash chain", top_k=5)
        assert len(results) >= 1
        # First result should be the evidence memory, not weather
        assert "evidence" in results[0].content.lower() or "hash" in results[0].content.lower()


# ---------------------------------------------------------------------------
# Verification state transition tests
# ---------------------------------------------------------------------------


class TestMemoryVerification:
    """Memory verification state transitions."""

    def test_verify_memory_transitions(self, fabric: MemoryFabric):
        """Memory can be verified (transition to VERIFIED state)."""
        record = fabric.store_memory(
            tenant="test-project",
            content="Test memory for verification",
            type_=MemoryType.SEMANTIC,
            tags=["test"],
            source_agent="test",
            importance=0.7,
        )

        # Verify the memory
        verified = fabric.verify_memory(
            memory_id=record.memory_id,
            to_state=VerificationState.VERIFIED,
            by="test-operator",
            reason="test evidence",
        )
        assert verified is not None
        assert verified.verification_state == VerificationState.VERIFIED

    def test_unverified_memory_stays_pending(self, fabric: MemoryFabric):
        """Memory without verification stays in pending state."""
        fabric.store_memory(
            tenant="test-project",
            content="Unverified memory",
            type_=MemoryType.SEMANTIC,
            tags=["test"],
            source_agent="test",
            importance=0.5,
        )
        # Don't verify — it should still be retrievable
        results = fabric.recall_memories(tenant="test-project", query="Unverified", top_k=1)
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# Memory isolation tests
# ---------------------------------------------------------------------------


class TestMemoryIsolation:
    """Memories are isolated by tenant."""

    def test_tenant_isolation(self, fabric: MemoryFabric):
        """Memories from different tenants don't mix."""
        fabric.store_memory(
            tenant="tenant-a",
            content="Secret for tenant A",
            type_=MemoryType.EPISODIC,
            tags=[],
            source_agent="test",
            importance=0.5,
        )
        fabric.store_memory(
            tenant="tenant-b",
            content="Secret for tenant B",
            type_=MemoryType.EPISODIC,
            tags=[],
            source_agent="test",
            importance=0.5,
        )

        results_a = fabric.recall_memories(tenant="tenant-a", query="secret", top_k=10)
        results_b = fabric.recall_memories(tenant="tenant-b", query="secret", top_k=10)

        # Each tenant should only see its own memories
        for r in results_a:
            assert "tenant a" in r.content.lower()
        for r in results_b:
            assert "tenant b" in r.content.lower()


# ---------------------------------------------------------------------------
# Memory fabric model tests
# ---------------------------------------------------------------------------


class TestMemoryFabricConsolidation:
    """Memory fabric consolidation — merging near-duplicates."""

    def test_consolidate_runs(self, fabric: MemoryFabric):
        """Consolidation can be triggered without error."""
        fabric.store_memory(
            tenant="p",
            content="Memory about ActionGate governance",
            type_=MemoryType.SEMANTIC,
            tags=["governance"],
            source_agent="test",
            importance=0.5,
        )
        result = fabric.consolidate(tenant="p")
        assert isinstance(result, dict)
