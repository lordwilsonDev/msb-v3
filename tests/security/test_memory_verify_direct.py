"""Direct fabric path tests for memory_verify (P10 dual-path).

These bypass the MCP bridge and hit MemoryFabric.verify_memory directly,
proving the core invariant independently of network/auth layers.
"""

from __future__ import annotations

import os
import sys
import sqlite3

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "src"))

from msb_v3.memory_fabric.fabric import MemoryFabric, MemoryFabricStore, MemoryType, VerificationState  # noqa: E402

DB_PATH = os.path.join(REPO, "data", "memory_fabric", "memory.db")


def _fabric() -> MemoryFabric:
    store = MemoryFabricStore(db_path=DB_PATH)
    return MemoryFabric(store=store)


# ---------------------------------------------------------------------------
# D1: direct legitimate transition
# ---------------------------------------------------------------------------
def test_d1_direct_legitimate():
    fab = _fabric()
    item = fab.store_memory(content="D1 direct legitimate", type_=MemoryType.SEMANTIC, tags=["p10"])
    verified = fab.verify_memory(item.memory_id, VerificationState.VERIFIED, by="pytest", reason="legit")
    assert verified.verification_state == VerificationState.VERIFIED


# ---------------------------------------------------------------------------
# D2: empty actor rejected
# ---------------------------------------------------------------------------
def test_d2_empty_actor_rejected():
    fab = _fabric()
    item = fab.store_memory(content="D2 empty actor", type_=MemoryType.SEMANTIC, tags=["p10"])
    with pytest.raises(ValueError, match="actor identity required"):
        fab.verify_memory(item.memory_id, VerificationState.VERIFIED, by="", reason="legit")


# ---------------------------------------------------------------------------
# D3: illegal transition rejected
# ---------------------------------------------------------------------------
def test_d3_illegal_transition():
    fab = _fabric()
    item = fab.store_memory(content="D3 illegal transition", type_=MemoryType.SEMANTIC, tags=["p10"])
    # Step to VERIFIED so we have a known source state
    fab.verify_memory(item.memory_id, VerificationState.VERIFIED, by="pytest", reason="step")
    # VERIFIED cannot go back to UNVERIFIED
    with pytest.raises(ValueError, match="illegal verification transition"):
        fab.verify_memory(item.memory_id, VerificationState.UNVERIFIED, by="pytest", reason="bad")


# ---------------------------------------------------------------------------
# D4: CONTRADICTED → VERIFIED requires resolution
# ---------------------------------------------------------------------------
def test_d4_contradicted_resolution_required():
    fab = _fabric()
    item = fab.store_memory(content="D4 contradiction resolution", type_=MemoryType.SEMANTIC, tags=["p10"])
    fab.verify_memory(item.memory_id, VerificationState.CONTRADICTED, by="pytest", reason="mark")
    with pytest.raises(ValueError, match="resolution evidence required"):
        fab.verify_memory(item.memory_id, VerificationState.VERIFIED, by="pytest", reason="retry")
    fab.verify_memory(item.memory_id, VerificationState.VERIFIED, by="pytest", reason="ok", resolution="cleared")
    assert fab.store.get(item.memory_id).verification_state == VerificationState.VERIFIED


# ---------------------------------------------------------------------------
# D5: hash chain preserved across transitions
# ---------------------------------------------------------------------------
def test_d5_hash_chain():
    fab = _fabric()
    item = fab.store_memory(content="D5 hash chain", type_=MemoryType.SEMANTIC, tags=["p10"])
    fab.verify_memory(item.memory_id, VerificationState.VERIFIED, by="pytest")
    fab.verify_memory(item.memory_id, VerificationState.CONTRADICTED, by="pytest", reason="issue")
    fab.verify_memory(item.memory_id, VerificationState.VERIFIED, by="pytest", reason="fix", resolution="done")

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM verification_history WHERE memory_id = ? ORDER BY id", (item.memory_id,)
        ).fetchall()]

    assert len(rows) == 3
    for i in range(1, len(rows)):
        prev_hash = rows[i - 1].get("record_hash", "")
        curr_prev = rows[i].get("previous_hash", "")
        if prev_hash and curr_prev:
            assert curr_prev == prev_hash
