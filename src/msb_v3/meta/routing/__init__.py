"""META-1B: Capability Router — selects the best worker for a compiled task.

The router doesn't ask "which is the best model?"
It asks: "which available worker has the highest expected probability of
completing this specific compiled task?"

Conceptually: P(success | worker, task, context, tools, constraints)

All workers are interchangeable.  The router chooses.  The kernel does not
care which implementation wins.
"""

from msb_v3.meta.routing.capability_matcher import CapabilityMatcher, MatchResult
from msb_v3.meta.routing.route_decision import RouteDecision
from msb_v3.meta.routing.skill_registry import RegisteredSkill, SkillRegistry
from msb_v3.meta.routing.worker_registry import RegisteredWorker, WorkerRegistry

__all__ = [
    "CapabilityMatcher",
    "MatchResult",
    "RegisteredSkill",
    "RegisteredWorker",
    "RouteDecision",
    "SkillRegistry",
    "WorkerRegistry",
]
