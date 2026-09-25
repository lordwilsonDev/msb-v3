"""Mission state machine — the frozen transition matrix.

Blueprint: docs/blueprints/2026-09-25-agent-control-plane.md §4.

The matrix is frozen the same way the memory-verify matrix is: changing an
edge means changing the snapshot pinned in
``tests/mission/test_mission_states.py`` in the same commit, on purpose.

Three rules live here and nowhere else:

* ``MERGED`` and ``ABANDONED`` are terminal — no exits.
* ``GATED_BLOCK`` never reaches ``MERGED``; a blocked mission goes back to
  ``BUILDING`` or is abandoned.
* ``BLOCKED_ON_HUMAN`` returns only to the state it came from (its
  ``resume_state``), or to ``ABANDONED``.
"""

from __future__ import annotations

from enum import Enum


class MissionState(str, Enum):
    DRAFT = "DRAFT"
    RESEARCHING = "RESEARCHING"
    BLUEPRINT_REVIEW = "BLUEPRINT_REVIEW"
    PLANNING = "PLANNING"
    PLAN_REVIEW = "PLAN_REVIEW"
    BUILDING = "BUILDING"
    VERIFYING = "VERIFYING"
    GATED_GREEN = "GATED_GREEN"
    GATED_REVIEW = "GATED_REVIEW"
    GATED_BLOCK = "GATED_BLOCK"
    MERGED = "MERGED"
    ABANDONED = "ABANDONED"
    BLOCKED_ON_HUMAN = "BLOCKED_ON_HUMAN"


S = MissionState

TERMINAL = frozenset({S.MERGED, S.ABANDONED})

# The verdicts only the MSB-v3 gate may issue (blueprint §9).
GATE_VERDICTS = frozenset({S.GATED_GREEN, S.GATED_REVIEW, S.GATED_BLOCK})

# Forward and rework edges. Every non-terminal state except BLOCKED_ON_HUMAN
# additionally reaches ABANDONED and BLOCKED_ON_HUMAN (see allowed_targets).
_EDGES: dict[MissionState, frozenset[MissionState]] = {
    S.DRAFT: frozenset({S.RESEARCHING}),
    S.RESEARCHING: frozenset({S.BLUEPRINT_REVIEW}),
    S.BLUEPRINT_REVIEW: frozenset({S.PLANNING, S.RESEARCHING}),
    S.PLANNING: frozenset({S.PLAN_REVIEW}),
    S.PLAN_REVIEW: frozenset({S.BUILDING, S.PLANNING}),
    S.BUILDING: frozenset({S.VERIFYING}),
    S.VERIFYING: frozenset({S.GATED_GREEN, S.GATED_REVIEW, S.GATED_BLOCK, S.BUILDING}),
    S.GATED_GREEN: frozenset({S.MERGED}),
    S.GATED_REVIEW: frozenset({S.MERGED, S.BUILDING}),
    S.GATED_BLOCK: frozenset({S.BUILDING}),
}

# Transitions that cross the authority boundary (blueprint §4). In addition,
# every move to ABANDONED and every return from BLOCKED_ON_HUMAN is a human
# decision (see is_human_gated).
HUMAN_GATED = frozenset({
    (S.BLUEPRINT_REVIEW, S.PLANNING),
    (S.PLAN_REVIEW, S.BUILDING),
    (S.GATED_GREEN, S.MERGED),
    (S.GATED_REVIEW, S.MERGED),
})


class MissionTransitionError(ValueError):
    """The requested move is not an edge of the frozen matrix."""


def allowed_targets(
    state: MissionState, resume_state: MissionState | None = None
) -> frozenset[MissionState]:
    """Every state ``state`` may move to. ``resume_state`` matters only for
    BLOCKED_ON_HUMAN, which returns to where it came from."""
    if state in TERMINAL:
        return frozenset()
    if state is S.BLOCKED_ON_HUMAN:
        if resume_state is None:
            return frozenset({S.ABANDONED})
        return frozenset({S.ABANDONED, resume_state})
    return _EDGES[state] | {S.ABANDONED, S.BLOCKED_ON_HUMAN}


def validate_transition(
    from_state: MissionState,
    to_state: MissionState,
    *,
    resume_state: MissionState | None = None,
) -> None:
    if to_state not in allowed_targets(from_state, resume_state):
        raise MissionTransitionError(
            f"illegal mission transition {from_state.value} -> {to_state.value}"
        )


def is_human_gated(from_state: MissionState, to_state: MissionState) -> bool:
    if to_state is S.ABANDONED:
        return True
    if from_state is S.BLOCKED_ON_HUMAN:
        return True
    return (from_state, to_state) in HUMAN_GATED
