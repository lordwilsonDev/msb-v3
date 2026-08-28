"""ExecutionPolicy — the first-class contract for execution shape selection.

An ExecutionPolicy answers: *How should this task be executed?*

It specifies not merely *which model* but the entire *shape of cognition*:

    EXECUTION SHAPE
          │
    ┌─────┼─────┐
    │     │     │
  Context Tools  Reasoning
    │     │     │
  FULL   FULL   OPEN
  COMPILED LIMITED STRUCTURED
  MINIMAL TASK-SPEC DECOMPOSED

Three execution modes:

    FABLE   — Maximum intelligence.  Full problem, full context, frontier model.
               Best for: novel architecture, ambiguous problems, $$$$.

    HYBRID  — High intelligence + decomposition.  Strong QC, local execution.
               Best for: software construction, $$–$$$.

    LOCAL   — Narrow but capable.  Tight spec, small model, high volume.
               Best for: repetitive/high-volume work, $.

The user can choose the mode OR META can choose it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionMode(str, Enum):
    """Three execution regimes on the intelligence spectrum."""

    FABLE = "FABLE"      # frontier + full context + maximum intelligence
    HYBRID = "HYBRID"    # decompose + local exec + strong QC
    LOCAL = "LOCAL"      # tight spec + small model + high volume


class ContextStrategy(str, Enum):
    """How much context to provide to the worker."""

    FULL = "FULL"           # entire problem context (Fable)
    COMPILED = "COMPILED"   # minimum-sufficient compiled context (Hybrid)
    MINIMAL = "MINIMAL"     # task-only, no repository context (Local)


class ToolStrategy(str, Enum):
    """Which tools the worker may access."""

    FULL = "FULL"           # all available tools (Fable)
    LIMITED = "LIMITED"     # curated tool subset (Hybrid)
    TASK_SPEC = "TASK_SPEC" # only tools the task explicitly requires (Local)


class ReasoningStrategy(str, Enum):
    """How the worker approaches the problem."""

    OPEN = "OPEN"           # unconstrained reasoning (Fable)
    STRUCTURED = "STRUCTURED"  # guided by contracts and schemas (Hybrid)
    DECOMPOSED = "DECOMPOSED"  # recursive decomposition into subtasks (Hybrid/Local)


class VerificationStrategy(str, Enum):
    """How strictly the result is verified."""

    STANDARD = "STANDARD"   # deterministic checks (Local)
    STRICT = "STRICT"       # deterministic + contract + integration (Hybrid)
    FUZZY = "FUZZY"         # deterministic + semantic + independent judge (Fable)


@dataclass
class ExecutionShape:
    """The complete execution shape — context × tools × reasoning × verification."""

    context: ContextStrategy = ContextStrategy.COMPILED
    tools: ToolStrategy = ToolStrategy.LIMITED
    reasoning: ReasoningStrategy = ReasoningStrategy.STRUCTURED
    verification: VerificationStrategy = VerificationStrategy.STRICT

    def to_dict(self) -> Dict[str, str]:
        return {
            "context": self.context.value,
            "tools": self.tools.value,
            "reasoning": self.reasoning.value,
            "verification": self.verification.value,
        }


@dataclass
class ExecutionPolicy:
    """The complete execution policy — how a task should be run.

    The Intelligence Router produces this.  It encodes not just *which worker*
    but *how* the worker should be constrained, what context it receives,
    how it should reason, and how its output should be verified.

    Usage::

        policy = PolicyRouter().route(meta_task)
        if policy.mode is ExecutionMode.HYBRID:
            worker = select_worker(policy.executor)
            context = compile_context(policy.shape.context)
            result = execute(worker, context, policy)
    """

    # Task identity.
    task_id: str

    # Execution mode — the primary strategic choice.
    mode: ExecutionMode = ExecutionMode.HYBRID

    # Execution shape — the detailed strategy within the mode.
    shape: ExecutionShape = field(default_factory=ExecutionShape)

    # Model assignments.
    planner: str = ""       # who plans the task (may be None for Local)
    executor: str = ""      # who executes
    verifier: str = ""      # who verifies (may be independent judge for Fable)

    # Parallelism and retry.
    parallelism: int = 1
    max_retries: int = 2

    # Escalation — where to go if this policy fails.
    escalation: Optional[ExecutionMode] = None  # e.g., HYBRID → FABLE

    # Cost and latency bounds.
    max_cost_usd: float = 0.0
    max_latency_ms: float = 0.0

    # Decision metadata.
    confidence: float = 0.0    # 0.0–1.0, how confident the router is
    reason: str = ""           # why this policy was chosen
    alternatives: List[str] = field(default_factory=list)  # other modes considered

    # Audit.
    created_at: str = field(default_factory=_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for evidence/audit trail."""
        return {
            "task_id": self.task_id,
            "mode": self.mode.value,
            "shape": self.shape.to_dict(),
            "planner": self.planner,
            "executor": self.executor,
            "verifier": self.verifier,
            "parallelism": self.parallelism,
            "max_retries": self.max_retries,
            "escalation": self.escalation.value if self.escalation else None,
            "max_cost_usd": self.max_cost_usd,
            "max_latency_ms": self.max_latency_ms,
            "confidence": self.confidence,
            "reason": self.reason,
            "alternatives": self.alternatives,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @staticmethod
    def fable(task_id: str, *, planner: str = "claude", executor: str = "claude",
              verifier: str = "independent_judge") -> ExecutionPolicy:
        """Preset: Fable mode — maximum intelligence, full context."""
        return ExecutionPolicy(
            task_id=task_id,
            mode=ExecutionMode.FABLE,
            shape=ExecutionShape(
                context=ContextStrategy.FULL,
                tools=ToolStrategy.FULL,
                reasoning=ReasoningStrategy.OPEN,
                verification=VerificationStrategy.FUZZY,
            ),
            planner=planner,
            executor=executor,
            verifier=verifier,
            parallelism=1,
            max_retries=1,
            escalation=None,  # nothing above Fable
        )

    @staticmethod
    def hybrid(task_id: str, *, planner: str = "frontier", executor: str = "qwen3:8b",
               verifier: str = "frontier") -> ExecutionPolicy:
        """Preset: Hybrid mode — decompose + local exec + strong QC."""
        return ExecutionPolicy(
            task_id=task_id,
            mode=ExecutionMode.HYBRID,
            shape=ExecutionShape(
                context=ContextStrategy.COMPILED,
                tools=ToolStrategy.LIMITED,
                reasoning=ReasoningStrategy.STRUCTURED,
                verification=VerificationStrategy.STRICT,
            ),
            planner=planner,
            executor=executor,
            verifier=verifier,
            parallelism=4,
            max_retries=2,
            escalation=ExecutionMode.FABLE,
        )

    @staticmethod
    def local(task_id: str, *, executor: str = "qwen3:8b",
              verifier: str = "pytest") -> ExecutionPolicy:
        """Preset: Local mode — tight spec, small model, high volume."""
        return ExecutionPolicy(
            task_id=task_id,
            mode=ExecutionMode.LOCAL,
            shape=ExecutionShape(
                context=ContextStrategy.MINIMAL,
                tools=ToolStrategy.TASK_SPEC,
                reasoning=ReasoningStrategy.DECOMPOSED,
                verification=VerificationStrategy.STANDARD,
            ),
            planner="",  # no planner — task is pre-compiled
            executor=executor,
            verifier=verifier,
            parallelism=8,
            max_retries=3,
            escalation=ExecutionMode.HYBRID,
        )
