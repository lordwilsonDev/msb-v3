"""Sovereign Runtime — Health System.

Returns component health status in a stable JSON shape.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List


@dataclass(frozen=True)
class ComponentHealth:
    name: str
    status: str
    detail: str = ""


@dataclass
class HealthReport:
    agent_id: str = "sovereign-agent-001"
    overall: str = "online"
    components: List[ComponentHealth] = field(default_factory=list)

    def to_dict(self) -> dict:
        result = asdict(self)
        result["components"] = [asdict(c) for c in self.components]
        return result


class HealthSystem:
    def __init__(self, agent_id: str = "sovereign-agent-001") -> None:
        self.agent_id = agent_id
        self._checks: Dict[str, Callable[..., Any]] = {}

    def register(self, name: str, check: Callable[..., Any]) -> None:
        self._checks[name] = check

    def check(self) -> HealthReport:
        components: List[ComponentHealth] = []
        for name, check in self._checks.items():
            try:
                ok = bool(check())
                components.append(
                    ComponentHealth(name=name, status="online" if ok else "offline")
                )
            except Exception as exc:  # pragma: no cover
                components.append(ComponentHealth(name=name, status="error", detail=str(exc)))
        overall = "online" if all(c.status == "online" for c in components) else "degraded"
        return HealthReport(agent_id=self.agent_id, overall=overall, components=components)
