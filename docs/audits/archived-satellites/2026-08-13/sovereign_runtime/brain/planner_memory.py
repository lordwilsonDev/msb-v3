from __future__ import annotations

from typing import Any, Dict, Optional


class PlannerMemory:
    def __init__(self) -> None:
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.by_goal: Dict[str, list[str]] = {}

    def record_plan_node(
        self,
        node_id: str,
        parent_id: Optional[str],
        goal: str,
        depth: int,
    ) -> None:
        self.nodes[node_id] = {
            "id": node_id,
            "parent_id": parent_id,
            "goal": goal,
            "depth": depth,
        }
        self.by_goal.setdefault(goal, []).append(node_id)

    def get(self, node_id: str) -> Dict[str, Any]:
        return self.nodes[node_id]

    def children(self, node_id: str) -> list[str]:
        return [nid for nid, node in self.nodes.items() if node.get("parent_id") == node_id]

    def goal_nodes(self, goal: str) -> list[str]:
        return list(self.by_goal.get(goal, []))
