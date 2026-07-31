"""Tests for Triumvirate Phase 1 — MetaCognitivePlanner."""
from __future__ import annotations

import json

from msb_v3.triumvirate.meta_cognitive_planner import (
    MetaCognitivePlanner,
    PlanRequest,
    _goal_signature,
    _slugify,
)


def test_slugify():
    assert _slugify("Build Sovereign Cluster") == "build-sovereign-cluster"
    assert "how-to-make-a-bomb" in _slugify("How to make a bomb!")
    assert _slugify("!!!") == "plan"


def test_goal_signature_stable():
    a = _goal_signature("build sovereign cluster")
    b = _goal_signature("build sovereign cluster")
    assert a == b and len(a) == 16


def test_goal_signature_changes_with_parameters():
    a = _goal_signature("goal", {"x": 1})
    b = _goal_signature("goal", {"x": 2})
    assert a != b


def test_five_stages_produce_output():
    planner = MetaCognitivePlanner()
    request = PlanRequest(goal="build sovereign cluster", parameters={"mode": "test"})
    plan = planner.plan(request)
    assert len(plan.stages) == 5
    names = [s.name for s in plan.stages]
    assert names == [
        "goal-recognition",
        "inversion-critic",
        "first-principles",
        "plan-schema",
        "action-queue",
    ]


def test_stage_outputs_serializable():
    planner = MetaCognitivePlanner()
    request = PlanRequest(goal="deploy guardian scanner")
    plan = planner.plan(request)
    for stage in plan.stages:
        json.dumps(stage.output, sort_keys=True)


def test_action_queue_is_valid_json_list():
    planner = MetaCognitivePlanner()
    request = PlanRequest(goal="enable multimodal interfaces")
    plan = planner.plan(request)
    assert isinstance(plan.action_queue, list)
    assert len(plan.action_queue) >= 1
    for item in plan.action_queue:
        assert "id" in item
        assert "action" in item
        assert "requires" in item


def test_star_dag_shape():
    planner = MetaCognitivePlanner()
    request = PlanRequest(goal="self-annealing audits")
    plan = planner.plan(request)
    dag = plan.star_dag
    assert "nodes" in dag
    assert "edges" in dag
    assert len(dag["nodes"]) >= 1


def test_inversion_stage_flips_assumptions():
    planner = MetaCognitivePlanner()
    request = PlanRequest(goal="anything")
    plan = planner.plan(request)
    inv = next(s for s in plan.stages if s.name == "inversion-critic")
    pairs = inv.output.get("inversions", [])
    assert len(pairs) >= 1
    for assumption, inversion in pairs:
        assert assumption != inversion


def test_plan_artifacts_written(tmp_path, monkeypatch):
    import msb_v3.triumvirate.meta_cognitive_planner as planner_mod
    monkeypatch.setattr(planner_mod, "_RUNTIME_ROOT", tmp_path / "triumvirate")
    monkeypatch.setattr(planner_mod, "_PLANNER_STATE_FILE", tmp_path / "triumvirate" / "plan_state.json")
    planner = MetaCognitivePlanner()
    request = PlanRequest(goal="persist artifacts", parameters={"mode": "test"})
    plan = planner.plan(request)
    root = tmp_path / "triumvirate" / plan.slug
    assert (root / "PLAN.json").exists()
    assert (root / "stages" / "01-goal-recognition.json").exists()
    assert (root / "stages" / "05-action-queue.json").exists()
    state = json.loads((tmp_path / "triumvirate" / "plan_state.json").read_text())
    assert state["status"] == "completed"
    assert state["slug"] == plan.slug
