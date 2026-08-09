from __future__ import annotations

import pytest

from sovereign_runtime.brain.plan_models import Action, PlanNode
from sovereign_runtime.brain.planner_memory import PlannerMemory
from sovereign_runtime.brain.recursive_planner import MAX_DEPTH, RecursivePlanner


class FakeMemory:
    def __init__(self):
        self.records = []

    def record_plan_node(self, node_id, parent_id, goal, depth):
        self.records.append({"node_id": node_id, "parent_id": parent_id, "goal": goal, "depth": depth})


def test_simple_goal_produces_single_action():
    planner = RecursivePlanner(memory=FakeMemory())
    node = planner.plan("Create hello.txt")
    assert node.status == "ready"
    assert len(node.actions) == 1
    assert node.actions[0].type == "log"
    assert node.actions[0].payload["message"] == "Create hello.txt"
    assert node.depth == 0
    assert node.children == []


def test_recursive_goal_creates_subgoals():
    planner = RecursivePlanner(memory=FakeMemory())
    goal = "Build a website with research, design, implementation, testing, and deployment phases"
    node = planner.plan(goal)
    assert node.status == "ready"
    assert len(node.children) == 2
    assert all(child.depth == 1 for child in node.children)
    assert node.children[0].goal == goal[: len(goal) // 2]
    assert node.children[1].goal == goal[len(goal) // 2 :]


def test_recursion_terminates_at_max_depth():
    planner = RecursivePlanner(memory=FakeMemory())
    node = planner.plan("x" * 200, depth=MAX_DEPTH)
    assert node.status == "terminated"
    assert node.actions == [Action(type="error", payload={"reason": "maximum recursion depth"})]


def test_planner_memory_records_nodes():
    memory = PlannerMemory()
    planner = RecursivePlanner(memory=memory)
    node = planner.plan("root", depth=0)
    assert len(memory.nodes) == 1
    assert memory.goal_nodes("root") == [memory.nodes.keys().__iter__().__next__()]


def test_planner_tree_round_trip():
    planner = RecursivePlanner(memory=None)
    node = planner.plan("Build a website")
    serialized = planner.to_dict(node)
    assert serialized["goal"] == "Build a website"
    assert "children" in serialized
    assert serialized["depth"] == 0
    assert serialized["status"] == "ready"
