"""Phase 3 — ReplayEngine: event-sourced state reconstruction.

Pins the contract: state is *derived* from the event log (not trusted from the
projection), transitions are validated against the state machine, projection
divergence and illegal transitions are surfaced (never healed), and the spine
decision trail joins the full reconstruction.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app
from msb_v3.core.config import settings
from msb_v3.core.container import build_container
from msb_v3.evidence.spine import DecisionEvidence, DecisionEvidenceStore
from msb_v3.replay.engine import ReplayEngine
from msb_v3.tasks.lifecycle import TaskLifecycle
from msb_v3.tasks.models import UnifiedTask
from msb_v3.uac.audit_chain import AuditChain


def _task(task_id: str = "t.1") -> UnifiedTask:
    return UnifiedTask(task_id=task_id, kind="agent.run", tenant="wilson-vault", session="s")


@pytest.fixture()
def lifecycle(tmp_path):
    chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
    return TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)


def _complete(lifecycle: TaskLifecycle, task_id: str = "t.1") -> None:
    lifecycle.create(_task(task_id))
    for state in ("PLANNED", "EXECUTING", "VERIFYING", "COMPLETED"):
        lifecycle.transition(task_id, state)


# --- replay_state ---------------------------------------------------------


def test_replay_state_derives_final_state_from_events(lifecycle):
    _complete(lifecycle)
    result = ReplayEngine(lifecycle).replay_state("t.1")
    assert result["derived_state"] == "COMPLETED"
    assert result["stored_state"] == "COMPLETED"
    assert result["consistent"] is True
    assert result["legal"] is True
    assert [t["to_state"] for t in result["transitions"]] == [
        "CREATED",
        "PLANNED",
        "EXECUTING",
        "VERIFYING",
        "COMPLETED",
    ]


def test_replay_state_detects_projection_divergence(lifecycle):
    _complete(lifecycle)
    # Tamper the projection only: the event log still ends at COMPLETED.
    with sqlite3.connect(lifecycle.db_path) as conn:
        conn.execute("UPDATE unified_tasks SET state=? WHERE task_id=?", ("FAILED", "t.1"))
    result = ReplayEngine(lifecycle).replay_state("t.1")
    assert result["derived_state"] == "COMPLETED"
    assert result["stored_state"] == "FAILED"
    assert result["consistent"] is False
    assert "divergence" in result


def test_replay_state_detects_illegal_transition(lifecycle):
    _complete(lifecycle)
    # Rewrite one event's state to an illegal jump (EXECUTING -> DENIED is not
    # in the state machine); the event log itself now carries a bad transition.
    with sqlite3.connect(lifecycle.db_path) as conn:
        conn.execute(
            "UPDATE task_events SET state=? WHERE task_id=? AND event_type=?",
            ("DENIED", "t.1", "VERIFICATION_STARTED"),
        )
    result = ReplayEngine(lifecycle).replay_state("t.1")
    assert result["legal"] is False
    assert any("illegal transition" in issue for issue in result["issues"])


def test_replay_state_unknown_task_raises(lifecycle):
    with pytest.raises(Exception):
        ReplayEngine(lifecycle).replay_state("nope")


# --- replay_task / replay_decision ----------------------------------------


def test_replay_task_joins_timeline_and_spine_decisions(lifecycle, tmp_path):
    _complete(lifecycle)
    spine = DecisionEvidenceStore(str(tmp_path / "spine.db"))
    spine.append(
        DecisionEvidence(
            task_id="t.1",
            policy_version="handle-gate-v1",
            policy_result="ALLOW",
            risk_level="normal",
        ),
        audit_seq=1,
    )
    result = ReplayEngine(lifecycle, spine=spine).replay_task("t.1")
    assert result["derived_state"] == "COMPLETED"
    assert [e["event_type"] for e in result["timeline"]] == [
        "TASK_CREATED",
        "PLAN_CREATED",
        "AGENT_STARTED",
        "VERIFICATION_STARTED",
        "TASK_COMPLETED",
    ]
    assert len(result["decisions"]) == 1
    assert result["decisions"][0]["policy_result"] == "ALLOW"


def test_replay_decision_empty_without_spine(lifecycle):
    _complete(lifecycle)
    assert ReplayEngine(lifecycle).replay_decision("t.1") == []


# --- reconcile ------------------------------------------------------------


def test_reconcile_reports_divergence_and_in_flight(lifecycle):
    _complete(lifecycle, "done")
    lifecycle.create(_task("in-flight"))
    lifecycle.transition("in-flight", "PLANNED")
    lifecycle.transition("in-flight", "EXECUTING")
    with sqlite3.connect(lifecycle.db_path) as conn:
        conn.execute("UPDATE unified_tasks SET state=? WHERE task_id=?", ("FAILED", "done"))

    result = ReplayEngine(lifecycle).reconcile()
    assert result["task_count"] == 2
    assert result["divergence_count"] == 1
    assert result["divergences"][0]["task_id"] == "done"
    assert result["in_flight"] == ["in-flight"]


# --- HTTP surface ---------------------------------------------------------


def test_agent_replay_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "operator_token", "test-token")
    lifecycle = TaskLifecycle(
        db_path=str(tmp_path / "tasks.db"),
        chain=AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True),
    )
    _complete(lifecycle, "api.1")
    app = create_app()
    app.state.container = build_container(
        replay=ReplayEngine(lifecycle, spine=DecisionEvidenceStore(str(tmp_path / "spine.db")))
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}

    r = client.get("/agent/tasks/api.1/replay", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["derived_state"] == "COMPLETED"
    assert body["consistent"] is True
    assert len(body["timeline"]) == 5

    assert client.get("/agent/tasks/nope/replay", headers=headers).status_code == 404
    # operator-gated: unset token -> closed (503)
    monkeypatch.setattr(settings, "operator_token", "")
    assert client.get("/agent/tasks/api.1/replay", headers=headers).status_code == 503
