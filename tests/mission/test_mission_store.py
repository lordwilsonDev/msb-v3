"""Mission store: creation, versions, staleness, transitions, chain evidence
(blueprint 2026-09-25 §4, Phase 1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from msb_ledger.audit_chain import AuditChain
from msb_v3.mission.models import VersionKind
from msb_v3.mission.states import MissionState as S
from msb_v3.mission.states import MissionTransitionError
from msb_v3.mission.store import (
    GATE_ACTOR,
    OPERATORS_ENV,
    MissionError,
    MissionStore,
    operators_from_env,
)


class RefusingChain:
    """An audit chain that is down."""

    def append(self, component: str, event_type: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("chain unavailable")


@pytest.fixture
def chain(tmp_path: Path) -> AuditChain:
    return AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)


@pytest.fixture
def store(tmp_path: Path, chain: AuditChain) -> MissionStore:
    return MissionStore(str(tmp_path / "missions.db"), chain=chain, operators={"wilson"})


def events(chain: AuditChain, event_type: str) -> list[Any]:
    return [r for r in chain.get_chain("missions") if r.event_type == event_type]


def to_plan_review(store: MissionStore, mid: str) -> None:
    store.transition(mid, S.RESEARCHING, actor="claude-pm", reason="start research")
    store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "todo board"}, created_by="claude-pm")
    store.transition(mid, S.BLUEPRINT_REVIEW, actor="claude-pm", reason="blueprint ready")
    store.transition(mid, S.PLANNING, actor="wilson", reason="ok", approved_by="wilson")
    store.add_version(mid, VersionKind.PLAN, {"tasks": ["T-1"]}, created_by="planner")
    store.transition(mid, S.PLAN_REVIEW, actor="planner", reason="plan ready")


def to_verifying(store: MissionStore, mid: str) -> None:
    to_plan_review(store, mid)
    store.transition(mid, S.BUILDING, actor="wilson", reason="ok", approved_by="wilson")
    store.transition(mid, S.VERIFYING, actor="hermes", reason="built")


# -- creation ---------------------------------------------------------------


def test_create_starts_in_draft_and_is_on_the_chain(
    store: MissionStore, chain: AuditChain
) -> None:
    m = store.create("Build a todo board", created_by="wilson", constraints={"budget_usd": 5})
    assert m.state is S.DRAFT
    assert m.mission_id.startswith("mission-")
    assert store.get(m.mission_id) == m
    created = events(chain, "mission.created")
    assert [e.payload["mission_id"] for e in created] == [m.mission_id]
    assert created[0].payload["constraints"] == {"budget_usd": 5}


@pytest.mark.parametrize(("objective", "who"), [("   ", "wilson"), ("todo", "  ")])
def test_create_requires_objective_and_author(
    store: MissionStore, objective: str, who: str
) -> None:
    with pytest.raises(MissionError):
        store.create(objective, created_by=who)


def test_non_canonical_constraints_are_refused(store: MissionStore) -> None:
    with pytest.raises(MissionError, match="canonical"):
        store.create("todo", created_by="wilson", constraints={"budget": float("inf")})


def test_unknown_mission(store: MissionStore) -> None:
    with pytest.raises(MissionError, match="unknown mission"):
        store.get("mission-nope")


def test_chain_refusal_records_nothing(tmp_path: Path) -> None:
    store = MissionStore(str(tmp_path / "m.db"), chain=RefusingChain(), operators={"wilson"})  # type: ignore[arg-type]
    with pytest.raises(MissionError, match="audit chain refused"):
        store.create("todo", created_by="wilson")
    conn = sqlite3.connect(tmp_path / "m.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 0
    finally:
        conn.close()


# -- versions and staleness -------------------------------------------------


def test_versions_are_numbered_and_hashed(store: MissionStore, chain: AuditChain) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    v1 = store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="claude-pm")
    v2 = store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "b"}, created_by="claude-pm")
    assert (v1.version, v2.version) == (1, 2)
    assert v1.sha256 != v2.sha256
    assert v1.parent_sha256 is None
    assert store.latest_version(mid, VersionKind.BLUEPRINT) == v2
    on_chain = events(chain, "mission.version")
    assert [e.payload["sha256"] for e in on_chain] == [v1.sha256, v2.sha256]
    assert "body" not in on_chain[0].payload


def test_plan_needs_a_blueprint_and_names_it(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    with pytest.raises(MissionError, match="none yet"):
        store.add_version(mid, VersionKind.PLAN, {"tasks": []}, created_by="planner")
    bp = store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="claude-pm")
    plan = store.add_version(mid, VersionKind.PLAN, {"tasks": []}, created_by="planner")
    assert plan.parent_sha256 == bp.sha256


def test_re_adding_identical_content_is_a_no_op(store: MissionStore, chain: AuditChain) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    first = store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="claude-pm")
    again = store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="someone")
    assert again == first
    assert len(events(chain, "mission.version")) == 1


def test_staleness_propagates_down_the_chain(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="claude-pm")
    store.add_version(mid, VersionKind.PLAN, {"tasks": ["T-1"]}, created_by="planner")
    store.add_version(mid, VersionKind.TASK_GRAPH, {"nodes": ["T-1"]}, created_by="planner")
    assert store.stale_versions(mid) == []

    store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "b"}, created_by="claude-pm")
    stale = {s.kind: s for s in store.stale_versions(mid)}
    assert set(stale) == {VersionKind.PLAN, VersionKind.TASK_GRAPH}
    assert "built against blueprint" in stale[VersionKind.PLAN].reason
    assert stale[VersionKind.TASK_GRAPH].reason == "its plan is stale"

    store.add_version(mid, VersionKind.PLAN, {"tasks": ["T-1"]}, created_by="planner")
    stale = {s.kind: s for s in store.stale_versions(mid)}
    assert set(stale) == {VersionKind.TASK_GRAPH}
    assert "built against plan" in stale[VersionKind.TASK_GRAPH].reason


def test_versions_and_transitions_are_append_only(tmp_path: Path, store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="claude-pm")
    store.transition(mid, S.RESEARCHING, actor="claude-pm", reason="start")
    conn = sqlite3.connect(tmp_path / "missions.db")
    try:
        for sql in (
            "UPDATE mission_versions SET body = '{}'",
            "DELETE FROM mission_versions",
            "UPDATE mission_transitions SET reason = 'x'",
            "DELETE FROM mission_transitions",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute(sql)
    finally:
        conn.close()


# -- transitions ------------------------------------------------------------


def test_full_path_to_merged(store: MissionStore, chain: AuditChain) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    to_verifying(store, mid)
    store.transition(mid, S.GATED_GREEN, actor=GATE_ACTOR, reason="all checks pass")
    m = store.transition(mid, S.MERGED, actor="wilson", reason="ship", approved_by="wilson")
    assert m.state is S.MERGED
    history = store.history(mid)
    assert [h["to_state"] for h in history][-2:] == ["GATED_GREEN", "MERGED"]
    assert len(events(chain, "mission.transition")) == len(history)


def test_illegal_transition_changes_nothing(store: MissionStore, chain: AuditChain) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    with pytest.raises(MissionTransitionError):
        store.transition(mid, S.BUILDING, actor="hermes", reason="skip ahead")
    assert store.get(mid).state is S.DRAFT
    assert events(chain, "mission.transition") == []


@pytest.mark.parametrize("approver", [None, "mallory"])
def test_human_gate_needs_a_configured_operator(store: MissionStore, approver: str | None) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    to_plan_review(store, mid)
    with pytest.raises(MissionError, match="configured operator"):
        store.transition(mid, S.BUILDING, actor="hermes", reason="go", approved_by=approver)
    assert store.get(mid).state is S.PLAN_REVIEW


def test_no_operators_configured_means_every_gate_is_refused(
    tmp_path: Path, chain: AuditChain
) -> None:
    store = MissionStore(str(tmp_path / "m.db"), chain=chain, operators=set())
    mid = store.create("todo", created_by="wilson").mission_id
    with pytest.raises(MissionError, match="configured operator"):
        store.transition(mid, S.ABANDONED, actor="wilson", reason="stop", approved_by="wilson")


def test_operators_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPERATORS_ENV, " wilson, ,ops-2 ")
    assert operators_from_env() == {"wilson", "ops-2"}
    monkeypatch.delenv(OPERATORS_ENV)
    assert operators_from_env() == frozenset()


def test_only_the_gate_issues_verdicts(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    to_verifying(store, mid)
    with pytest.raises(MissionError, match="gate verdicts"):
        store.transition(mid, S.GATED_GREEN, actor="claude-verify", reason="PASS")
    assert store.transition(mid, S.GATED_BLOCK, actor=GATE_ACTOR, reason="tests fail").state is S.GATED_BLOCK


def test_block_verdict_cannot_merge_even_with_approval(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    to_verifying(store, mid)
    store.transition(mid, S.GATED_BLOCK, actor=GATE_ACTOR, reason="tests fail")
    with pytest.raises(MissionTransitionError):
        store.transition(mid, S.MERGED, actor="wilson", reason="ship anyway", approved_by="wilson")


def test_blocked_on_human_resumes_where_it_left_off(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    to_plan_review(store, mid)
    store.transition(mid, S.BUILDING, actor="wilson", reason="ok", approved_by="wilson")
    blocked = store.transition(mid, S.BLOCKED_ON_HUMAN, actor="hermes", reason="need a key")
    assert blocked.resume_state is S.BUILDING
    with pytest.raises(MissionError, match="configured operator"):
        store.transition(mid, S.BUILDING, actor="hermes", reason="resume")
    with pytest.raises(MissionTransitionError):
        store.transition(mid, S.VERIFYING, actor="wilson", reason="jump", approved_by="wilson")
    resumed = store.transition(mid, S.BUILDING, actor="wilson", reason="key given", approved_by="wilson")
    assert (resumed.state, resumed.resume_state) == (S.BUILDING, None)


def test_blueprint_review_needs_a_blueprint(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    store.transition(mid, S.RESEARCHING, actor="claude-pm", reason="start")
    with pytest.raises(MissionError, match="needs a blueprint"):
        store.transition(mid, S.BLUEPRINT_REVIEW, actor="claude-pm", reason="nothing yet")


def test_plan_review_needs_a_plan(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    store.transition(mid, S.RESEARCHING, actor="claude-pm", reason="start")
    store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="claude-pm")
    store.transition(mid, S.BLUEPRINT_REVIEW, actor="claude-pm", reason="ready")
    store.transition(mid, S.PLANNING, actor="wilson", reason="ok", approved_by="wilson")
    with pytest.raises(MissionError, match="needs a plan"):
        store.transition(mid, S.PLAN_REVIEW, actor="planner", reason="nothing yet")


def test_stale_plan_cannot_start_building(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    to_plan_review(store, mid)
    store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "changed"}, created_by="claude-pm")
    with pytest.raises(MissionError, match="stale"):
        store.transition(mid, S.BUILDING, actor="wilson", reason="ok", approved_by="wilson")
    store.transition(mid, S.PLANNING, actor="planner", reason="replan")
    store.add_version(mid, VersionKind.PLAN, {"tasks": ["T-1", "T-2"]}, created_by="planner")
    store.transition(mid, S.PLAN_REVIEW, actor="planner", reason="plan v2")
    assert store.transition(mid, S.BUILDING, actor="wilson", reason="ok", approved_by="wilson").state is S.BUILDING


def test_terminal_mission_takes_nothing(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    store.transition(mid, S.ABANDONED, actor="wilson", reason="not needed", approved_by="wilson")
    with pytest.raises(MissionTransitionError):
        store.transition(mid, S.RESEARCHING, actor="wilson", reason="revive", approved_by="wilson")
    with pytest.raises(MissionError, match="no new versions"):
        store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="claude-pm")


def test_chain_refusal_leaves_the_state_unchanged(tmp_path: Path, chain: AuditChain) -> None:
    db = str(tmp_path / "m.db")
    good = MissionStore(db, chain=chain, operators={"wilson"})
    mid = good.create("todo", created_by="wilson").mission_id
    down = MissionStore(db, chain=RefusingChain(), operators={"wilson"})  # type: ignore[arg-type]
    with pytest.raises(MissionError, match="audit chain refused"):
        down.transition(mid, S.RESEARCHING, actor="claude-pm", reason="start")
    assert good.get(mid).state is S.DRAFT
    assert good.history(mid) == []
