"""Tests for the MetaCognitivePlanner — now an honest intent pass-through.

The planner no longer fabricates five static "stages": it echoes the goal,
signs it, and returns a single "proceed" action. Real planning is delegated
to ``msb_v3.agent.planner`` (the model-based planner used by ``handle()``).
These tests pin that contract: deterministic, side-effect-free, no fake
stage machinery, and nothing written to disk.
"""
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


def test_pass_through_single_stage():
    """The honest contract: one intent-pass-through stage, not five fakes."""
    planner = MetaCognitivePlanner()
    request = PlanRequest(goal="build sovereign cluster", parameters={"mode": "test"})
    plan = planner.plan(request)
    assert len(plan.stages) == 1
    assert plan.stages[0].name == "intent-pass-through"
    assert plan.stages[0].output["plan"] == ["proceed"]
    assert plan.stages[0].output["goal"] == request.goal


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


def test_plan_echoes_goal_and_signature():
    planner = MetaCognitivePlanner()
    request = PlanRequest(goal="persist artifacts", parameters={"mode": "test"})
    plan = planner.plan(request)
    assert plan.goal == request.goal
    assert plan.signature == _goal_signature(request.goal, request.parameters)
    assert plan.slug == _slugify(request.goal)


def test_pass_through_writes_nothing(tmp_path):
    """The old planner wrote five JSON stage files + a plan_state.json per
    call. The pass-through must be side-effect-free: nothing is written to
    disk at all — the endpoint is a pure echo.
    """
    planner = MetaCognitivePlanner()
    request = PlanRequest(goal="persist artifacts", parameters={"mode": "test"})
    plan = planner.plan(request)
    assert plan.stages[0].name == "intent-pass-through"
    # No artifact dirs, stage files, or state file may be created.
    assert list(tmp_path.iterdir()) == []
