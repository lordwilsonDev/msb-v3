from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class Action:
    type: str
    payload: Dict


@dataclass
class PlanNode:
    goal: str
    depth: int = 0
    status: str = "pending"
    children: List["PlanNode"] = field(default_factory=list)
    actions: List[Action] = field(default_factory=list)
    history: List[str] = field(default_factory=list)
