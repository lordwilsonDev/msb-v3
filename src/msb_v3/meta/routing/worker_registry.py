"""WorkerRegistry — the interchangeable worker management layer.

Blueprint §6, §9, §17:
    All workers are interchangeable.  The router selects; the kernel does
    not care which implementation wins.

    Worker tiers: Qwen 3B → Qwen 8B → DeepSeek → Claude → Gemini →
    Google Skill → human/operator.

    Multi-worker escalation: Qwen 3B fails → threshold → larger worker.

The WorkerRegistry discovers, registers, and queries workers.  It extends
the existing ``ProviderRegistry`` pattern from ``agent/providers.py`` but
operates at the Meta-System level.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RegisteredWorker:
    """A registered worker in the Meta-System's worker registry."""

    worker_id: str
    display_name: str = ""
    kind: str = ""  # "local" | "cli" | "api" | "skill" | "paseo" | "human"
    model_id: str = ""  # e.g. "qwen3-3b", "deepseek-v3", "claude-sonnet"
    provider_id: str = ""  # links to agent/providers.py ProviderSpec

    capabilities: List[str] = field(default_factory=list)
    negative_capabilities: List[str] = field(default_factory=list)
    max_risk_tier: int = 2
    max_context_tokens: int = 8192
    preferred_task_types: List[str] = field(default_factory=list)

    cost_per_1k_tokens: float = 0.0
    avg_latency_ms: float = 0.0
    available: bool = True

    metadata: Dict[str, Any] = field(default_factory=dict)

    def can_handle(self, task_type: str) -> bool:
        """True if this worker's preferred types include the task type."""
        if not self.preferred_task_types:
            return True
        return task_type in self.preferred_task_types

    def has_capability(self, capability: str) -> bool:
        """True if this worker declares the given capability."""
        return capability in self.capabilities

    def blocks(self, capability: str) -> bool:
        """True if this worker explicitly rejects the given capability."""
        return capability in self.negative_capabilities


class WorkerRegistry:
    """Discovers, registers, and queries interchangeable workers.

    Workers are discovered from:
        1. Explicit registration (local models, API providers)
        2. Provider registry bridge (agent/providers.py)
        3. Skill registry bridge (meta/routing/skill_registry.py)

    Usage::

        registry = WorkerRegistry()
        registry.register(RegisteredWorker(
            worker_id="qwen3b",
            model_id="qwen3-3b",
            kind="local",
            capabilities=["python", "testing"],
            max_context_tokens=8192,
        ))

        candidates = registry.find_workers(
            capabilities=["python"],
            task_type="implementation",
        )
    """

    def __init__(self) -> None:
        self._workers: Dict[str, RegisteredWorker] = {}

    def register(self, worker: RegisteredWorker) -> None:
        """Register a worker.  Overwrites if worker_id already exists."""
        self._workers[worker.worker_id] = worker
        logger.debug("registered worker: %s (%s)", worker.worker_id, worker.kind)

    def unregister(self, worker_id: str) -> bool:
        """Remove a worker.  Returns True if it existed."""
        if worker_id in self._workers:
            del self._workers[worker_id]
            return True
        return False

    def get(self, worker_id: str) -> Optional[RegisteredWorker]:
        """Look up a worker by id."""
        return self._workers.get(worker_id)

    def list_all(self) -> List[RegisteredWorker]:
        """List all registered workers."""
        return list(self._workers.values())

    def list_available(self) -> List[RegisteredWorker]:
        """List only available workers."""
        return [w for w in self._workers.values() if w.available]

    def find_workers(
        self,
        *,
        capabilities: Optional[List[str]] = None,
        negative_filter: Optional[List[str]] = None,
        task_type: Optional[str] = None,
        max_risk_tier: int = 4,
        max_context_tokens: Optional[int] = None,
        available_only: bool = True,
    ) -> List[RegisteredWorker]:
        """Find workers matching the given criteria.

        A worker matches if:
            - it provides ALL requested capabilities
            - it does NOT provide any negative_filter capabilities
            - its risk_tier <= max_risk_tier
            - if task_type specified, it can_handle(task_type)
            - if max_context_tokens specified, its capacity >= the budget
            - if available_only, it is available
        """
        results: List[RegisteredWorker] = []
        cap_set = set(capabilities) if capabilities else set()
        neg_set = set(negative_filter) if negative_filter else set()

        for worker in self._workers.values():
            if available_only and not worker.available:
                continue
            if cap_set and not cap_set.issubset(set(worker.capabilities)):
                continue
            if neg_set and neg_set.intersection(set(worker.capabilities)):
                continue
            if worker.max_risk_tier > max_risk_tier:
                continue
            if task_type and not worker.can_handle(task_type):
                continue
            if max_context_tokens and worker.max_context_tokens < max_context_tokens:
                continue
            results.append(worker)

        return results

    def escalate(self, current_worker_id: str) -> Optional[RegisteredWorker]:
        """Given the current worker, find the next tier up for escalation.

        Escalation order: 3B → 8B → DeepSeek → Claude → Gemini → human.
        """
        escalation_order = [
            "qwen3-3b", "qwen3-8b", "deepseek-v3", "claude-sonnet",
            "gemini-pro", "human",
        ]

        current = self.get(current_worker_id)
        current_model = current.model_id if current else current_worker_id

        try:
            idx = escalation_order.index(current_model)
        except ValueError:
            # Unknown model — try to find a larger worker by context size.
            if current:
                return self._find_larger(current)
            return None

        # Find the next tier.
        for model_id in escalation_order[idx + 1:]:
            for worker in self._workers.values():
                if worker.model_id == model_id and worker.available:
                    return worker

        return None

    def _find_larger(self, current: RegisteredWorker) -> Optional[RegisteredWorker]:
        """Find an available worker with more capacity than *current*."""
        best: Optional[RegisteredWorker] = None
        for worker in self._workers.values():
            if not worker.available:
                continue
            if worker.max_context_tokens > current.max_context_tokens:
                if best is None or worker.max_context_tokens < best.max_context_tokens:
                    best = worker
        return best

    def bridge_from_provider_registry(self, provider_registry: Any) -> int:
        """Import workers from the existing ``ProviderRegistry`` (agent/providers.py).

        Returns the number of workers imported.
        """
        count = 0
        for info in provider_registry.list():
            worker = RegisteredWorker(
                worker_id=info["provider_id"],
                display_name=info["display_name"],
                kind=info["kind"],
                provider_id=info["provider_id"],
                capabilities=list(info.get("capabilities", [])),
                max_risk_tier=info.get("max_risk_tier", 2),
                available=info.get("available", True),
            )
            self.register(worker)
            count += 1
        return count
