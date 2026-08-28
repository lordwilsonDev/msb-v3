"""TaskTranslator — the semantic-preserving compilation engine.

Blueprint §8:
    Translation is semantic-preserving compilation.

    It may modify: wording, structure, verbosity, examples, tool syntax,
    instruction order.

    It may NOT modify: objective, constraints, authorization, success criteria,
    verification requirements, safety requirements.

The translator receives a ``MetaTask`` + a worker profile + a context selection
and produces a ``ModelTask`` — the bounded envelope the worker actually sees.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from msb_v3.meta.contracts import MetaTask
from msb_v3.meta.translation.context_compiler import ContextCompiler, ContextSelection
from msb_v3.meta.translation.model_task import ContextBudget, ModelTask, ToolPolicy

logger = logging.getLogger(__name__)


@dataclass
class WorkerProfile:
    """Describes a worker's capabilities, constraints, and preferences.

    The router produces this; the translator consumes it.
    """

    worker_id: str
    worker_name: str = ""
    capabilities: List[str] = field(default_factory=list)
    negative_capabilities: List[str] = field(default_factory=list)
    max_context_tokens: int = 8192
    preferred_task_types: List[str] = field(default_factory=list)
    tool_restrictions: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    examples: List[Dict[str, str]] = field(default_factory=list)  # few-shot examples

    def can_handle(self, task_type: str) -> bool:
        """True if this worker's preferred types include the task type."""
        if not self.preferred_task_types:
            return True  # no preference = accepts all
        return task_type in self.preferred_task_types


class TaskTranslator:
    """Translates a ``MetaTask`` into a ``ModelTask`` for a specific worker.

    The translation pipeline:
        1. Context compiler selects minimum-sufficient context.
        2. Tool policy is derived from worker capabilities + task constraints.
        3. Constraints are merged (task constraints override worker defaults).
        4. Success criteria are carried from the MetaTask.
        5. Translation notes record what changed (audit trail).

    Usage::

        translator = TaskTranslator(context_compiler=ContextCompiler(...))
        model_task = translator.translate(
            meta_task,
            worker_profile=WorkerProfile(worker_id="qwen3b", ...),
        )
    """

    def __init__(
        self,
        *,
        context_compiler: Optional[ContextCompiler] = None,
    ) -> None:
        self._context_compiler = context_compiler or ContextCompiler()

    def translate(
        self,
        task: MetaTask,
        *,
        worker: WorkerProfile,
        budget: Optional[ContextBudget] = None,
    ) -> ModelTask:
        """Translate *task* into a worker-specific ``ModelTask``.

        The core contract: ``task.objective`` is preserved semantically.
        Everything else may be adapted to the worker's profile.
        """
        # 1. Compile context.
        budget = budget or ContextBudget(total_tokens=worker.max_context_tokens)
        selection = self._context_compiler.compile(
            task,
            budget=budget,
            worker_capabilities=worker.capabilities,
        )

        # 2. Build tool policy.
        tool_policy = self._build_tool_policy(task, worker)

        # 3. Merge constraints.
        constraints = self._merge_constraints(task, worker)

        # 4. Build translation notes (audit trail).
        notes = self._build_translation_notes(task, worker, selection)

        # 5. Build the ModelTask.
        model_task = ModelTask(
            model_task_id=f"MT-{uuid.uuid4().hex[:8]}",
            source_task_id=task.task_id,
            worker_id=worker.worker_id,
            objective=task.objective,
            task_type=task.task_type,
            context_files=selection.files,
            context_content=selection.file_contents,
            context_tests=selection.tests,
            context_architecture=selection.architecture_refs,
            context_dependencies=selection.dependency_refs,
            tool_policy=tool_policy,
            constraints=constraints,
            inputs={},
            expected_output=task.metadata.get("expected_output", {}),
            success_criteria=task.metadata.get("success_criteria", {}),
            verification_commands=task.metadata.get("verification_commands", []),
            max_file_changes=constraints.get("max_files_changed", 10),
            max_attempts=3,
            complexity=task.complexity,
            priority=task.priority,
            budget=selection.budget,
            translation_notes=notes,
            metadata={
                "worker_name": worker.worker_name,
                "translation_version": "v1",
                "context_rejected_count": len(selection.rejected),
            },
        )

        return model_task

    # -- internal helpers ---------------------------------------------------

    def _build_tool_policy(self, task: MetaTask, worker: WorkerProfile) -> ToolPolicy:
        """Derive allowed/forbidden tools from worker capabilities + task constraints."""
        allowed = list(worker.capabilities)
        forbidden = list(worker.negative_capabilities)

        # Task-level tool constraints.
        task_constraints = task.metadata.get("constraints", {})
        if "allowed_tools" in task_constraints:
            allowed = list(task_constraints["allowed_tools"])
        if "forbidden_tools" in task_constraints:
            forbidden.extend(task_constraints["forbidden_tools"])

        # Worker-level restrictions.
        for tool, config in worker.tool_restrictions.items():
            if config is False:
                forbidden.append(tool)

        return ToolPolicy(
            allowed=allowed,
            forbidden=forbidden,
            tool_configs=worker.tool_restrictions,
        )

    def _merge_constraints(self, task: MetaTask, worker: WorkerProfile) -> Dict[str, Any]:
        """Merge task and worker constraints. Task constraints take precedence."""
        merged = dict(worker.constraints)
        task_constraints = task.metadata.get("constraints", {})
        merged.update(task_constraints)
        return merged

    def _build_translation_notes(
        self,
        task: MetaTask,
        worker: WorkerProfile,
        selection: ContextSelection,
    ) -> List[str]:
        """Record what changed during translation for audit."""
        notes: List[str] = []

        notes.append(f"translated for worker: {worker.worker_id}")
        notes.append(f"context files: {len(selection.files)} included, "
                      f"{len(selection.rejected)} rejected by budget")

        if selection.budget.utilization > 0.8:
            notes.append(f"WARNING: context budget utilization high ({selection.budget.utilization:.0%})")

        if worker.examples:
            notes.append(f"worker examples available: {len(worker.examples)}")

        if task.complexity:
            notes.append(f"complexity: {task.complexity.value}")

        return notes
