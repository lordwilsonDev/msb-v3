from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from sovereign_runtime.brain.plan_models import Action, PlanNode  # type: ignore


MAX_DEPTH = 10


class RecursivePlanner:
    def __init__(self, memory: Any) -> None:
        self.memory = memory

    def plan(
        self,
        goal: str,
        depth: int = 0,
        parent_id: Optional[str] = None,
        history: Optional[List[str]] = None,
    ) -> PlanNode:
        if history is None:
            history = []

        node_id = str(uuid.uuid4())
        node = PlanNode(goal=goal, depth=depth, history=list(history))

        if self.memory is not None:
            self.memory.record_plan_node(node_id=node_id, parent_id=parent_id, goal=goal, depth=depth)

        if depth >= MAX_DEPTH:
            node.status = "terminated"
            node.actions = [Action(type="error", payload={"reason": "maximum recursion depth"})]
            return node

        decision = self.analyze(goal)
        if decision["type"] == "simple":
            node.status = "ready"
            node.actions = [Action(type=decision["action"]["type"], payload=decision["action"].get("payload", {}))]
            return node

        for subgoal in decision.get("subgoals", []):
            child = self.plan(subgoal, depth=depth + 1, parent_id=node_id, history=history + [goal])
            node.children.append(child)

        node.status = "ready"
        return node

    def analyze(self, goal: str) -> Dict[str, Any]:
        if len(goal) < 80:
            return {
                "type": "simple",
                "action": {"type": "log", "payload": {"message": goal}},
            }

        midpoint = len(goal) // 2
        return {
            "type": "complex",
            "subgoals": [goal[:midpoint], goal[midpoint:]],
        }

    def to_dict(self, node: PlanNode) -> Dict[str, Any]:
        return {
            "goal": node.goal,
            "depth": node.depth,
            "status": node.status,
            "actions": [{"type": a.type, "payload": a.payload} for a in node.actions],
            "children": [self.to_dict(child) for child in node.children],
            "history": node.history,
        }
