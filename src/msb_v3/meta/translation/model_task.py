"""ModelTask — the translated, worker-specific task representation.

A ``ModelTask`` is what a worker *actually receives*.  It is the output of
translating a ``MetaTask`` through the ``TaskTranslator`` with worker-specific
context, tool policy, and constraints applied.

The critical invariant: ``MetaTask.objective == ModelTask.objective`` at the
semantic level.  Translation may rephrase, restructure, add examples, or
change tool syntax — but the *meaning* is preserved (blueprint §8).

A ``ModelTask`` is NOT a prompt.  It is a structured specification that a
per-model prompt compiler consumes.  This survives model replacement (M3, M12).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from msb_v3.meta.contracts import Complexity, TaskState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ToolPolicy:
    """Allowed and forbidden tools/actions for this specific worker task.

    The translation layer populates this from the worker's capability
    declaration and the task's constraints.  A worker may only use tools
    explicitly listed in ``allowed``; everything in ``forbidden`` is
    rejected even if the worker attempts it.
    """

    allowed: List[str] = field(default_factory=list)
    forbidden: List[str] = field(default_factory=list)
    tool_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def is_allowed(self, tool: str) -> bool:
        """True if the tool is explicitly allowed and not forbidden."""
        if tool in self.forbidden:
            return False
        if not self.allowed:
            return True  # no allowlist = all tools permitted (except forbidden)
        return tool in self.allowed


@dataclass
class ContextBudget:
    """Token/context budget for a translated task.

    The context compiler fills this; the prompt compiler respects it.
    ``total_tokens`` is the hard ceiling; ``reserved_tokens`` holds space
    for system instructions, examples, and output.
    """

    total_tokens: int = 8192
    reserved_tokens: int = 2048
    available_tokens: int = 6144

    # How many files/references were included vs. available.
    files_included: int = 0
    files_available: int = 0
    tests_included: int = 0
    tests_available: int = 0

    @property
    def utilization(self) -> float:
        """Fraction of available context consumed (0.0–1.0)."""
        if self.available_tokens <= 0:
            return 1.0
        used = self.total_tokens - self.reserved_tokens - self.available_tokens
        return max(0.0, min(1.0, used / max(1, self.total_tokens - self.reserved_tokens)))


@dataclass
class ModelTask:
    """The translated, worker-specific task that a worker actually receives.

    Structure mirrors the blueprint §8 Worker Envelope:

        WORKER ENVELOPE
        ├── Task
        ├── Context
        ├── Allowed Tools
        ├── Constraints
        ├── Inputs
        ├── Expected Output
        ├── Success Criteria
        ├── Verification
        └── Stop Condition

    ``source_task_id`` traces back to the original ``MetaTask.task_id``.
    ``worker_id`` names the specific worker this was translated for.
    ``translation_notes`` records what changed during translation (for audit).
    """

    # Identity — traces to the MetaTask.
    model_task_id: str
    source_task_id: str
    worker_id: str

    # The objective (semantically identical to MetaTask.objective).
    objective: str

    # Worker-specific task type.
    task_type: str = "implementation"

    # Context the worker receives — compiled, not dumped.
    context_files: List[str] = field(default_factory=list)
    context_content: Dict[str, str] = field(default_factory=dict)  # path → content snippet
    context_tests: List[str] = field(default_factory=list)
    context_architecture: List[str] = field(default_factory=list)
    context_dependencies: List[str] = field(default_factory=list)

    # Tool policy — what this worker may/cannot use.
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy)

    # Constraints — hard limits the worker must respect.
    constraints: Dict[str, Any] = field(default_factory=dict)

    # Inputs — specific data/files the worker needs.
    inputs: Dict[str, str] = field(default_factory=dict)

    # Expected output — what success looks like structurally.
    expected_output: Dict[str, Any] = field(default_factory=dict)

    # Success criteria — the verification commands and boolean checks.
    success_criteria: Dict[str, bool] = field(default_factory=dict)
    verification_commands: List[str] = field(default_factory=list)

    # Stop conditions — when the worker must halt.
    max_file_changes: int = 10
    max_attempts: int = 3

    # Complexity and priority (carried from MetaTask).
    complexity: Optional[Complexity] = None
    priority: str = "P2"

    # Budget — how much context this worker gets.
    budget: ContextBudget = field(default_factory=ContextBudget)

    # Translation audit — what changed during translation.
    translation_notes: List[str] = field(default_factory=list)

    # Metadata.
    created_at: str = field(default_factory=_now)
    state: TaskState = TaskState.READY
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_worker_envelope(self) -> Dict[str, Any]:
        """Serialize into the Worker Envelope shape the blueprint specifies."""
        return {
            "task_id": self.model_task_id,
            "source_task_id": self.source_task_id,
            "worker_id": self.worker_id,
            "objective": self.objective,
            "task_type": self.task_type,
            "context": {
                "files": self.context_files,
                "content": self.context_content,
                "tests": self.context_tests,
                "architecture": self.context_architecture,
                "dependencies": self.context_dependencies,
            },
            "tools": {
                "allowed": self.tool_policy.allowed,
                "forbidden": self.tool_policy.forbidden,
            },
            "constraints": self.constraints,
            "inputs": self.inputs,
            "expected_output": self.expected_output,
            "success_criteria": self.success_criteria,
            "verification_commands": self.verification_commands,
            "stop_conditions": {
                "max_file_changes": self.max_file_changes,
                "max_attempts": self.max_attempts,
            },
            "budget": {
                "total_tokens": self.budget.total_tokens,
                "available_tokens": self.budget.available_tokens,
                "utilization": self.budget.utilization,
            },
            "translation_notes": self.translation_notes,
        }
