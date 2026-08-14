"""Sovereign Runtime — Health System.

Returns component health status in a stable JSON shape.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List


@dataclass(frozen=True)
class ComponentHealth:
    """One component's health-row — name + status + optional detail.

    `status` is one of `online` / `offline` / `error`. Frozen so a row
    once written can't be silently mutated downstream.
    """
    name: str
    status: str
    detail: str = ""


@dataclass
class HealthReport:
    """Whole-system health aggregate. Renders to JSON via `to_dict` for the home dashboard + `/health`."""
    agent_id: str = "sovereign-agent-001"
    overall: str = "online"
    components: List[ComponentHealth] = field(default_factory=list)

    def to_dict(self) -> dict:
        result = asdict(self)
        result["components"] = [asdict(c) for c in self.components]
        return result


class HealthSystem:
    """Health-aggregator — run a bag of registered check callables and roll up a JSON shape.

    Components register via `register(name, check)`; `check()` runs them
    in insertion order, captures exceptions as `status="error"` rows,
    and reports the overall as `online` only when *every* component
    reported `online`. Backs the home-dashboard health panel and
    `/system/health`.
    """
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
                components.append(
                    ComponentHealth(name=name, status="error", detail=str(exc))
                )
        overall = (
            "online" if all(c.status == "online" for c in components) else "degraded"
        )
        return HealthReport(
            agent_id=self.agent_id, overall=overall, components=components
        )
