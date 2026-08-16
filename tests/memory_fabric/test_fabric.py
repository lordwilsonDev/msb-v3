"""Memory Fabric operations — store, recall, verification state machine,
soft-delete forget, consolidation, and the decay model (spec §17)."""

import time

import pytest

from msb_v3.memory_fabric.fabric import MemoryFabric
from msb_v3.memory_fabric.models import MemoryType, VerificationState
from msb_v3.memory_fabric.store import MemoryFabricStore


@pytest.fixture()
def fabric(tmp_path):
    store = MemoryFabricStore(str(tmp_path / "memory.db"))
    return MemoryFabric(store)


def test_store_creates_unverified_memory(fabric):
    item = fabric.store_memory("the vault holds truth", project="msb", tags=["vault"])
    assert item.memory_id
    assert item.verification_state == VerificationState.UNVERIFIED
    assert item.type == MemoryType.SEMANTIC
    stored = fabric.store.get(item.memory_id)
    assert stored is not None and stored.content == "the vault holds truth"


def test_store_requires_content(fabric):
    with pytest.raises(ValueError):
        fabric.store_memory("   ")


def test_store_all_memory_types(fabric):
    for t in MemoryType:
        item = fabric.store_memory(f"{t.value} memory", type_=t)
        assert item.type == t


def test_recall_ranks_by_keyword_and_importance(fabric):
    fabric.store_memory("the fabric uses sqlite for storage", tags=["sqlite"], importance=0.9, project="msb")
    fabric.store_memory("the vault is at ~/Documents/Vault", tags=["vault"], importance=0.3, project="msb")
    hits = fabric.recall_memories("sqlite storage", project="msb")
    assert hits
    assert hits[0].content == "the fabric uses sqlite for storage"


def test_recall_respects_project_filter(fabric):
    fabric.store_memory("alpha decision", project="project-a", importance=0.9)
    fabric.store_memory("beta decision", project="project-b", importance=0.9)
    hits = fabric.recall_memories("decision", project="project-a")
    assert len(hits) == 1
    assert hits[0].project == "project-a"


def test_recall_returns_verification_state(fabric):
    item = fabric.store_memory("verified fact", tags=["fact"])
    fabric.verify_memory(item.memory_id, VerificationState.VERIFIED, by="operator", reason="checked")
    hits = fabric.recall_memories("verified fact")
    assert hits[0].verification_state == "VERIFIED"


def test_recall_touches_last_accessed(fabric):
    item = fabric.store_memory("rarely recalled", tags=["rare"])
    fabric.recall_memories("rarely recalled")
    refreshed = fabric.store.get(item.memory_id)
    assert refreshed.access_count == 1


def test_verify_transitions_are_legal(fabric):
    item = fabric.store_memory("fact")
    fabric.verify_memory(item.memory_id, VerificationState.VERIFIED, by="operator", reason="evidence")
    updated = fabric.store.get(item.memory_id)
    assert updated.verification_state == VerificationState.VERIFIED
    hist = fabric.store.verification_history(item.memory_id)
    assert len(hist) == 1
    assert hist[0]["from_state"] == "UNVERIFIED"
    assert hist[0]["to_state"] == "VERIFIED"
    assert hist[0]["by"] == "operator"


def test_verify_illegal_transition_is_rejected(fabric):
    item = fabric.store_memory("fact")
    # UNVERIFIED -> DEPRECATED is legal; but VERIFIED -> UNVERIFIED is not
    fabric.verify_memory(item.memory_id, VerificationState.VERIFIED, by="operator")
    with pytest.raises(ValueError, match="illegal verification transition"):
        fabric.verify_memory(item.memory_id, VerificationState.UNVERIFIED, by="operator")


def test_verify_unknown_memory_raises(fabric):
    with pytest.raises(KeyError):
        fabric.verify_memory("nope", VerificationState.VERIFIED)


def test_forget_is_soft_delete(fabric):
    item = fabric.store_memory("forgettable", tags=["temp"])
    fabric.forget_memory(item.memory_id, reason="no longer relevant")
    stored = fabric.store.get(item.memory_id)
    assert stored.archived is True  # row preserved (provenance)
    assert stored.verification_state == VerificationState.DEPRECATED
    hits = fabric.recall_memories("forgettable")
    assert hits == []  # never recalled again


def test_deprecated_is_terminal(fabric):
    item = fabric.store_memory("terminal")
    fabric.forget_memory(item.memory_id)
    with pytest.raises(ValueError, match="archived"):
        fabric.verify_memory(item.memory_id, VerificationState.VERIFIED)


def test_consolidate_merges_duplicates(fabric):
    a = fabric.store_memory("the api listens on port 8766", tags=["api"], project="msb", importance=0.8)
    b = fabric.store_memory("the api listens on port 8766 (confirmed)", tags=["api"], project="msb", importance=0.6)
    result = fabric.consolidate("default", by="operator")
    assert result["merged"] == 1
    assert b.memory_id in result["deprecations"]
    survivor = fabric.store.get(a.memory_id)
    assert not survivor.archived
    assert b.memory_id in survivor.relationships
    # the merged-away memory is deprecated
    assert fabric.store.get(b.memory_id).verification_state == VerificationState.DEPRECATED


def test_consolidate_decays_importance(fabric):
    item = fabric.store_memory("old memory", tags=["old"], importance=0.9)
    # simulate 40 days of no access (decay period is 30 days)
    store = fabric.store
    with store._lock:
        conn = store._conn()
        conn.execute(
            "UPDATE memory_items SET last_accessed_at=? WHERE memory_id=?",
            (time.time() - 40 * 24 * 3600, item.memory_id),
        )
        conn.commit()
        conn.close()
    result = fabric.consolidate("default")
    assert result["decayed"] >= 1
    refreshed = fabric.store.get(item.memory_id)
    assert refreshed.importance < 0.9


def test_live_score_decays_with_age():
    from msb_v3.memory_fabric.models import MemoryItem, recency_factor

    fresh = MemoryItem(memory_id="a", type=MemoryType.SEMANTIC, content="x", importance=0.9)
    old = MemoryItem(memory_id="b", type=MemoryType.SEMANTIC, content="x", importance=0.9)
    old.last_accessed_at = time.time() - 90 * 24 * 3600  # 90 days
    assert old.live_score() < fresh.live_score()
    # recency factor: 0.9 per 30 days
    assert recency_factor(0.9, 0) == 1.0
    assert recency_factor(0.9, 30 * 24 * 3600) == pytest.approx(0.9, rel=0.02)
    assert recency_factor(0.9, 90 * 24 * 3600) == pytest.approx(0.9 ** 3, rel=0.02)
    # floor: a memory never fully vanishes from scoring
    assert recency_factor(0.9, 3650 * 24 * 3600) == pytest.approx(0.1, rel=0.02)  # ~10 years
