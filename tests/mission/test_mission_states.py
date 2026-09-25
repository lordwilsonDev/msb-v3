"""The frozen mission transition matrix (blueprint 2026-09-25 §4)."""

from __future__ import annotations

import pytest

from msb_v3.mission.states import (
    GATE_VERDICTS,
    TERMINAL,
    MissionTransitionError,
    allowed_targets,
    is_human_gated,
    validate_transition,
)
from msb_v3.mission.states import (
    MissionState as S,
)

# The whole matrix, pinned. Changing an edge means changing this snapshot in
# the same commit, on purpose. BLOCKED_ON_HUMAN is shown with resume BUILDING.
EXPECTED = {
    "DRAFT": ["ABANDONED", "BLOCKED_ON_HUMAN", "RESEARCHING"],
    "RESEARCHING": ["ABANDONED", "BLOCKED_ON_HUMAN", "BLUEPRINT_REVIEW"],
    "BLUEPRINT_REVIEW": ["ABANDONED", "BLOCKED_ON_HUMAN", "PLANNING", "RESEARCHING"],
    "PLANNING": ["ABANDONED", "BLOCKED_ON_HUMAN", "PLAN_REVIEW"],
    "PLAN_REVIEW": ["ABANDONED", "BLOCKED_ON_HUMAN", "BUILDING", "PLANNING"],
    "BUILDING": ["ABANDONED", "BLOCKED_ON_HUMAN", "VERIFYING"],
    "VERIFYING": [
        "ABANDONED", "BLOCKED_ON_HUMAN", "BUILDING",
        "GATED_BLOCK", "GATED_GREEN", "GATED_REVIEW",
    ],
    "GATED_GREEN": ["ABANDONED", "BLOCKED_ON_HUMAN", "MERGED"],
    "GATED_REVIEW": ["ABANDONED", "BLOCKED_ON_HUMAN", "BUILDING", "MERGED"],
    "GATED_BLOCK": ["ABANDONED", "BLOCKED_ON_HUMAN", "BUILDING"],
    "MERGED": [],
    "ABANDONED": [],
    "BLOCKED_ON_HUMAN": ["ABANDONED", "BUILDING"],
}


def test_matrix_is_frozen() -> None:
    snapshot = {
        s.value: sorted(
            t.value
            for t in allowed_targets(s, S.BUILDING if s is S.BLOCKED_ON_HUMAN else None)
        )
        for s in S
    }
    assert snapshot == EXPECTED


def test_happy_path_is_legal() -> None:
    path = [
        S.DRAFT, S.RESEARCHING, S.BLUEPRINT_REVIEW, S.PLANNING, S.PLAN_REVIEW,
        S.BUILDING, S.VERIFYING, S.GATED_GREEN, S.MERGED,
    ]
    for a, b in zip(path, path[1:]):
        validate_transition(a, b)


def test_block_verdict_can_never_merge() -> None:
    with pytest.raises(MissionTransitionError):
        validate_transition(S.GATED_BLOCK, S.MERGED)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (S.RESEARCHING, S.PLANNING),
        (S.PLANNING, S.BUILDING),
        (S.BUILDING, S.GATED_GREEN),
        (S.DRAFT, S.MERGED),
        (S.VERIFYING, S.MERGED),
    ],
)
def test_skipping_a_stage_is_illegal(a: S, b: S) -> None:
    with pytest.raises(MissionTransitionError):
        validate_transition(a, b)


@pytest.mark.parametrize("terminal", sorted(TERMINAL, key=lambda s: s.value))
def test_terminal_states_have_no_exits(terminal: S) -> None:
    assert allowed_targets(terminal) == frozenset()


def test_every_working_state_can_block_on_human_and_be_abandoned() -> None:
    for s in S:
        if s in TERMINAL or s is S.BLOCKED_ON_HUMAN:
            continue
        assert {S.BLOCKED_ON_HUMAN, S.ABANDONED} <= allowed_targets(s)


def test_blocked_on_human_returns_only_to_where_it_came_from() -> None:
    assert allowed_targets(S.BLOCKED_ON_HUMAN, S.BUILDING) == {S.BUILDING, S.ABANDONED}
    with pytest.raises(MissionTransitionError):
        validate_transition(S.BLOCKED_ON_HUMAN, S.VERIFYING, resume_state=S.BUILDING)


def test_blocked_on_human_without_resume_state_can_only_be_abandoned() -> None:
    assert allowed_targets(S.BLOCKED_ON_HUMAN) == {S.ABANDONED}


def test_human_gates() -> None:
    assert is_human_gated(S.BLUEPRINT_REVIEW, S.PLANNING)
    assert is_human_gated(S.PLAN_REVIEW, S.BUILDING)
    assert is_human_gated(S.GATED_GREEN, S.MERGED)
    assert is_human_gated(S.GATED_REVIEW, S.MERGED)
    assert is_human_gated(S.BUILDING, S.ABANDONED)
    assert is_human_gated(S.BLOCKED_ON_HUMAN, S.BUILDING)
    assert not is_human_gated(S.DRAFT, S.RESEARCHING)
    assert not is_human_gated(S.BUILDING, S.BLOCKED_ON_HUMAN)
    assert not is_human_gated(S.VERIFYING, S.GATED_GREEN)


def test_gate_verdicts() -> None:
    assert GATE_VERDICTS == {S.GATED_GREEN, S.GATED_REVIEW, S.GATED_BLOCK}
