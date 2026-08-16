"""The unified task object (unified-architecture §27).

Every meaningful unit of work in the system eventually reduces to one
durable task object with a fixed section map:

    identity / intent / context / assumptions / inversion / plan / agents /
    capabilities / contracts / approvals / execution / observations /
    verification / evidence / audit / memory / outcome / recovery

``UnifiedTask`` is a mutable document (a task lives and changes) that
serializes to a flat JSON body for the durable store. ``from_dag_task()``
adapts the agent executor's frozen DAG ``Task`` into the plan section so the
existing executor machinery plugs in without rewriting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class UnifiedTask:
    # --- identity -------------------------------------------------------
    task_id: str
    kind: str = "agent.run"  # agent.run | vesta.approval | workflow | ...
    tenant: str = "default"
    session: str = "default"
    source: str = "api"  # api | cli | automation
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    state: str = "CREATED"  # lifecycle state machine (tasks/events.py)
    schema_version: int = 1

    # --- §27 sections ----------------------------------------------------
    intent: Dict[str, Any] = field(default_factory=dict)  # request, summary, privacy, permissions
    context: Dict[str, Any] = field(default_factory=dict)  # system, history, metadata
    assumptions: List[str] = field(default_factory=list)
    inversion: Dict[str, Any] = field(default_factory=dict)  # started, completed, findings
    plan: Dict[str, Any] = field(default_factory=dict)  # goal, source, tasks[]
    agents: List[Dict[str, Any]] = field(default_factory=list)  # agent_id, provider, model
    capabilities: Dict[str, Any] = field(default_factory=dict)  # required[], granted[]
    contracts: List[Dict[str, Any]] = field(default_factory=list)  # kind, status, ref
    approvals: List[Dict[str, Any]] = field(default_factory=list)  # kind, status, by, at, reason
    execution: Dict[str, Any] = field(default_factory=dict)  # started_at, finished_at, attempts, error
    observations: List[Dict[str, Any]] = field(default_factory=list)
    verification: Dict[str, Any] = field(default_factory=dict)  # method, verdict, detail
    evidence: List[str] = field(default_factory=list)  # evidence refs (hashes / paths)
    audit: List[Dict[str, Any]] = field(default_factory=list)  # {seq, event_type} chain refs
    memory: Dict[str, Any] = field(default_factory=dict)  # stored, refs
    outcome: Dict[str, Any] = field(default_factory=dict)  # ok, verdict, deterministic_hash, summary
    recovery: Dict[str, Any] = field(default_factory=dict)  # attempts, quarantined, recovered_at

    # --- serialization --------------------------------------------------

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "tenant": self.tenant,
            "session": self.session,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "state": self.state,
            "schema_version": self.schema_version,
            "intent": self.intent,
            "context": self.context,
            "assumptions": self.assumptions,
            "inversion": self.inversion,
            "plan": self.plan,
            "agents": self.agents,
            "capabilities": self.capabilities,
            "contracts": self.contracts,
            "approvals": self.approvals,
            "execution": self.execution,
            "observations": self.observations,
            "verification": self.verification,
            "evidence": self.evidence,
            "audit": self.audit,
            "memory": self.memory,
            "outcome": self.outcome,
            "recovery": self.recovery,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UnifiedTask":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def touch(self) -> None:
        self.updated_at = _now()

    def merge_sections(self, sections: Dict[str, Any]) -> None:
        """Merge §27 sections into the document (missing sections untouched)."""
        for key, value in sections.items():
            if key in self.__dataclass_fields__:
                setattr(self, key, value)
        self.touch()


def from_dag_task(task_id: str, goal: str, tasks: List[Any], *, source: str = "template") -> Dict[str, Any]:
    """Build the plan section from an agent DAG (``TaskGraph``).

    Keeps the executor's frozen ``Task`` shape inside the plan so existing
    executor/verifier machinery consumes it unchanged (convergence, not
    duplication — §32).
    """
    return {
        "goal": goal,
        "source": source,
        "tasks": [t.as_dict() if hasattr(t, "as_dict") else dict(t) for t in tasks],
    }


def dag_task_to_plan(tasks: List[Any], *, source: str = "template") -> Dict[str, Any]:
    goal = tasks[0].goal if tasks else ""
    return from_dag_task("", goal, tasks, source=source)


def adapt_dag(task_id: str, dag_task: Any) -> Optional[Dict[str, Any]]:
    """One frozen DAG Task -> a plan-section entry (best-effort)."""
    if dag_task is None:
        return None
    if hasattr(dag_task, "as_dict"):
        return dag_task.as_dict()
    return dict(dag_task)
