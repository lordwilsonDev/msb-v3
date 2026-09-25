"""Random walks over the mission store never break its invariants.

Whatever sequence of transitions, actors, approvals and new versions is
thrown at a mission, the recorded history must satisfy the blueprint's rules
(blueprint 2026-09-25 §4, §9) and match the audit chain one-for-one.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import event, given, settings
from hypothesis import strategies as st

from msb_ledger.audit_chain import AuditChain
from msb_v3.mission.models import VersionKind
from msb_v3.mission.states import (
    GATE_VERDICTS,
    TERMINAL,
    MissionState,
    MissionTransitionError,
    allowed_targets,
    is_human_gated,
)
from msb_v3.mission.store import GATE_ACTOR, MissionError, MissionStore

ACTORS = ["claude-pm", "planner", "hermes", GATE_ACTOR, "wilson"]

# Uniformly random targets almost never get past BLUEPRINT_REVIEW, which would
# leave BUILDING and the gate verdicts untested. So three steps in four pick a
# legal forward move (never ABANDONED or BLOCKED_ON_HUMAN, which stall or end
# the walk; wild steps still reach both). The approver and the new versions
# stay random, so the store's own refusals (missing operator, missing or stale
# plan, wrong gate actor) still fire. Half the walks start from a mission
# already in VERIFYING, so the gate verdicts and MERGED are exercised on every
# run rather than by luck.
steps = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=3),            # 0 = wild step
        st.integers(min_value=0, max_value=5),            # which legal target
        st.sampled_from(list(MissionState)),              # wild target
        st.sampled_from(ACTORS),
        st.sampled_from(["wilson", "wilson", None, "mallory"]),
        st.sampled_from([None, None, VersionKind.BLUEPRINT, VersionKind.PLAN, VersionKind.TASK_GRAPH]),
    ),
    min_size=1,
    max_size=60,
)


def _advance_to_verifying(store: MissionStore, mid: str) -> None:
    S = MissionState
    store.transition(mid, S.RESEARCHING, actor="claude-pm", reason="start")
    store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "walk"}, created_by="claude-pm")
    store.transition(mid, S.BLUEPRINT_REVIEW, actor="claude-pm", reason="ready")
    store.transition(mid, S.PLANNING, actor="wilson", reason="ok", approved_by="wilson")
    store.add_version(mid, VersionKind.PLAN, {"tasks": ["T-1"]}, created_by="planner")
    store.transition(mid, S.PLAN_REVIEW, actor="planner", reason="ready")
    store.transition(mid, S.BUILDING, actor="wilson", reason="ok", approved_by="wilson")
    store.transition(mid, S.VERIFYING, actor="hermes", reason="built")


_SIDE_EXITS = frozenset({MissionState.ABANDONED, MissionState.BLOCKED_ON_HUMAN})


def _pick_target(store: MissionStore, mid: str, mode: int, pick: int, wild: MissionState) -> MissionState:
    mission = store.get(mid)
    legal = sorted(
        (t for t in allowed_targets(mission.state, mission.resume_state) if t not in _SIDE_EXITS),
        key=lambda s: s.value,
    )
    if mode == 0 or not legal:
        return wild
    return legal[pick % len(legal)]


@settings(max_examples=200, deadline=None)
@given(st.booleans(), steps)
def test_random_walks_never_break_invariants(
    start_verifying: bool,
    walk: list[tuple[int, int, MissionState, str, str | None, VersionKind | None]],
) -> None:
    with tempfile.TemporaryDirectory() as d:
        chain = AuditChain(str(Path(d) / "audit.db"), allow_keyless=True)
        store = MissionStore(str(Path(d) / "m.db"), chain=chain, operators={"wilson"})
        mid = store.create("walk", created_by="wilson").mission_id
        if start_verifying:
            _advance_to_verifying(store, mid)

        for n, (mode, pick, wild, actor, approved_by, new_version) in enumerate(walk):
            if new_version is not None:
                try:
                    store.add_version(mid, new_version, {"n": n}, created_by=actor)
                except MissionError:
                    pass  # terminal mission, or no parent version yet
            target = _pick_target(store, mid, mode, pick, wild)
            if mode != 0 and target in GATE_VERDICTS:
                actor = GATE_ACTOR
            before = store.get(mid)
            try:
                store.transition(mid, target, actor=actor, reason=f"step {n}", approved_by=approved_by)
            except (MissionError, MissionTransitionError):
                assert store.get(mid) == before
            event(f"reached {store.get(mid).state.value}")

        history = store.history(mid)
        on_chain = [r for r in chain.get_chain("missions") if r.event_type == "mission.transition"]
        assert len(on_chain) == len(history)
        for row, record in zip(history, on_chain):
            a, b = MissionState(row["from_state"]), MissionState(row["to_state"])
            assert (record.payload["from_state"], record.payload["to_state"]) == (a.value, b.value)
            assert a not in TERMINAL
            assert (a, b) != (MissionState.GATED_BLOCK, MissionState.MERGED)
            if is_human_gated(a, b):
                assert row["approved_by"] == "wilson"
            if b in GATE_VERDICTS:
                assert row["actor"] == GATE_ACTOR
