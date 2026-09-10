"""P14 property-based testing: invariants across random valid/invalid inputs."""
from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))

from hypothesis import assume, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

import msb_v3.memory_fabric.fabric as fab_mod  # noqa: E402
import msb_v3.memory_fabric.store as store_mod  # noqa: E402
from msb_v3.memory_fabric.models import MemoryType, VerificationState  # noqa: E402


def _fresh_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


# ---------------------------------------------------------------------------
# P14-A: any non-empty content stores successfully
# ---------------------------------------------------------------------------
@given(content=st.sampled_from([
    "alpha", "bravo", "charlie", "delta", "echo",
    "foxtrot", "golf", "hotel", "india", "juliet",
    "kilo", "lima", "mike", "november", "oscar",
    "papa", "quebec", "romeo", "sierra", "tango",
    "uniform", "victor", "whiskey", "xray", "yankee", "zulu",
]))
@settings(max_examples=60)
def test_p14a_any_content_stores(content: str):
    path = _fresh_db()
    try:
        store = store_mod.MemoryFabricStore(path)
        f = fab_mod.MemoryFabric(store)
        item = f.store_memory(content=content, type_=MemoryType.SEMANTIC, tags=["p14"])
        assert item.memory_id
        assert item.content == content
        assert item.verification_state == VerificationState.UNVERIFIED
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# P14-B: same-state re-verify is rejected (no self-loops in transition map)
# ---------------------------------------------------------------------------
@given(state=st.sampled_from([
    VerificationState.UNVERIFIED,
    VerificationState.VERIFIED,
    VerificationState.CONTRADICTED,
    VerificationState.DEPRECATED,
]))
@settings(max_examples=40)
def test_p14b_same_state_rejected(state: VerificationState):
    path = _fresh_db()
    try:
        store = store_mod.MemoryFabricStore(path)
        f = fab_mod.MemoryFabric(store)
        item = f.store_memory(content="prop", type_=MemoryType.SEMANTIC, tags=["p14"])
        # Set source state
        try:
            f.verify_memory(item.memory_id, state, by="p14", reason="first")
        except Exception:
            pass
        # Same-state retry must raise (no self-loops)
        try:
            f.verify_memory(item.memory_id, state, by="p14", reason="second")
            assert False, f"same-state {state.value}->{state.value} was accepted"
        except ValueError:
            pass
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# P14-C: illegal transitions are always rejected
# ---------------------------------------------------------------------------
@given(from_state=st.sampled_from(list(VerificationState)), to_state=st.sampled_from(list(VerificationState)))
@settings(max_examples=80)
def test_p14c_illegal_transition_rejected(from_state: VerificationState, to_state: VerificationState):
    assume(from_state != to_state)
    allowed = {
        "UNVERIFIED": {"VERIFIED", "CONTRADICTED", "DEPRECATED"},
        "VERIFIED": {"CONTRADICTED", "DEPRECATED"},
        "CONTRADICTED": {"VERIFIED", "DEPRECATED"},
        "DEPRECATED": set(),
    }
    assume(to_state.value not in allowed.get(from_state.value, set()))

    path = _fresh_db()
    try:
        store = store_mod.MemoryFabricStore(path)
        f = fab_mod.MemoryFabric(store)
        item = f.store_memory(content="prop", type_=MemoryType.SEMANTIC, tags=["p14"])
        # Set source state
        try:
            f.verify_memory(item.memory_id, from_state, by="p14", reason="set")
        except Exception:
            pass
        # Illegal target must raise
        try:
            f.verify_memory(item.memory_id, to_state, by="p14", reason="bad")
            assert False, f"illegal {from_state.value}->{to_state.value} was accepted"
        except ValueError:
            pass
    finally:
        os.unlink(path)
