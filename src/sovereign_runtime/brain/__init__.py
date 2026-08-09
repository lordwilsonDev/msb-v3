from __future__ import annotations

from typing import Any, Dict, Optional

from sovereign_runtime.brain.plan_models import PlanNode
from sovereign_runtime.brain.planner_memory import PlannerMemory
from sovereign_runtime.brain.recursive_planner import RecursivePlanner
from sovereign_runtime.events.event_bus import Event, EventBus
from sovereign_runtime.core.identity import identity


class BrainService:
    def __init__(self, bus: Optional[EventBus] = None) -> None:
        self.bus = bus or EventBus()
        self.memory = PlannerMemory()
        self.planner = RecursivePlanner(memory=self.memory)
        self._plan_counter = 0
        self.bus.subscribe("agent.goal.received", self._on_goal_received)

    def _on_goal_received(self, event: Event) -> None:
        goal = str(event.payload.get("goal", "")).strip()
        if not goal:
            return

        plan_root = self.planner.plan(goal)
        plan_payload = {
            "goal": goal,
            "plan": self.planner.to_dict(plan_root),
            "created_by": identity.id,
        }
        self.bus.emit("agent.plan.created", plan_payload)
        self.bus.emit("agent.execute.request", {"plan": plan_payload["plan"]})

    def health(self) -> Dict[str, Any]:
        return {"name": "brain", "status": "online" if self.bus else "degraded"}
