"""Agent task DAG model — executable plans, not text (blueprint Layer 3).

A TaskGraph is a set of Tasks connected by parent_id edges. Tasks carry the
blueprint's full schema: goal, inputs, constraints, required capabilities,
tools, permissions, expected output, grounded verification method, timeout,
retry policy. The executor (T1.3) walks the graph in topological order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Task:
    task_id: str
    goal: str
    parent_id: Optional[str] = None
    inputs: Tuple[Dict[str, Any], ...] = ()
    constraints: Tuple[str, ...] = ()
    required_capabilities: Tuple[str, ...] = ()
    tools: Tuple[str, ...] = ()
    permissions: Tuple[str, ...] = ()
    expected_output: str = ""
    # Grounded verification key — resolved by the verifier registry (T1.4):
    #   "search_returned_hits" | "synthesis_nonempty" | "file_written" | "none"
    verification_method: str = "none"
    timeout_s: float = 60.0
    retry_policy: str = "retry:2"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "parent_id": self.parent_id,
            "inputs": list(self.inputs),
            "constraints": list(self.constraints),
            "required_capabilities": list(self.required_capabilities),
            "tools": list(self.tools),
            "permissions": list(self.permissions),
            "expected_output": self.expected_output,
            "verification_method": self.verification_method,
            "timeout_s": self.timeout_s,
            "retry_policy": self.retry_policy,
        }


@dataclass(frozen=True)
class TaskGraph:
    goal: str
    tasks: Tuple[Task, ...]
    source: str = "template"  # "template" | "llm"

    def by_id(self, task_id: str) -> Optional[Task]:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        return None

    def children_of(self, task_id: str) -> Tuple[Task, ...]:
        return tuple(t for t in self.tasks if t.parent_id == task_id)

    def roots(self) -> Tuple[Task, ...]:
        return tuple(t for t in self.tasks if t.parent_id is None)

    def order(self) -> List[Task]:
        """Topological order (parents before children), deterministic.

        Kahn's algorithm with ready nodes dequeued in task_id order, so the
        same graph always yields the same execution order.
        """
        indegree: Dict[str, int] = {t.task_id: 0 for t in self.tasks}
        for t in self.tasks:
            if t.parent_id is not None and t.parent_id in indegree:
                indegree[t.task_id] = indegree.get(t.task_id, 0) + 1
        ready = sorted([tid for tid, d in indegree.items() if d == 0])
        ordered: List[Task] = []
        while ready:
            tid = ready.pop(0)
            task = self.by_id(tid)
            if task is None:
                continue
            ordered.append(task)
            for child in sorted(self.children_of(tid), key=lambda c: c.task_id):
                indegree[child.task_id] -= 1
                if indegree[child.task_id] == 0:
                    ready.append(child.task_id)
                    ready.sort()
        if len(ordered) != len(self.tasks):
            # Cycle in the graph — the planner must never emit one; the
            # executor treats this as a hard failure rather than an infinite
            # loop. Deterministic ordering is still attempted for the acyclic
            # subset, but the caller should check is_acyclic().
            remaining = {t.task_id for t in self.tasks} - {t.task_id for t in ordered}
            raise ValueError(f"task graph contains a cycle: {sorted(remaining)}")
        return ordered

    def is_acyclic(self) -> bool:
        try:
            self.order()
            return True
        except ValueError:
            return False
