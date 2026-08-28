"""CapabilityMatcher — scores how well a worker matches a task's requirements.

Blueprint §9:
    The router asks: "which available worker has the highest expected
    probability of completing this specific compiled task?"

The CapabilityMatcher produces raw capability scores; the probability engine
(META-1C) combines these with historical performance and cost to produce the
final route decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from msb_v3.meta.contracts import Complexity, MetaTask
from msb_v3.meta.routing.worker_registry import RegisteredWorker


@dataclass
class MatchResult:
    """How well a worker matches a specific task."""

    worker_id: str
    task_id: str

    # Component scores (0.0–1.0).
    capability_score: float = 0.0
    specificity_score: float = 0.0
    risk_score: float = 0.0
    context_score: float = 0.0
    availability_score: float = 0.0

    # Overall match score (weighted average).
    overall_score: float = 0.0

    # Flags.
    blocked: bool = False
    block_reasons: List[str] = field(default_factory=list)
    matched_capabilities: List[str] = field(default_factory=list)
    missing_capabilities: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "capability_score": self.capability_score,
            "specificity_score": self.specificity_score,
            "risk_score": self.risk_score,
            "context_score": self.context_score,
            "availability_score": self.availability_score,
            "overall_score": self.overall_score,
            "blocked": self.blocked,
        }


class CapabilityMatcher:
    """Matches task requirements to worker capabilities.

    Scoring weights (blueprint §10):
        Capability Match       30%
        Task Specificity       20%
        Risk Compatibility     15%
        Context Fit            15%
        Availability            5%
        (Historical Success    15% — added by probability engine)

    Usage::

        matcher = CapabilityMatcher()
        results = matcher.match(task, workers)
        best = max(results, key=lambda r: r.overall_score)
    """

    # Default weights (sum to 1.0).
    WEIGHTS = {
        "capability": 0.30,
        "specificity": 0.20,
        "risk": 0.15,
        "context": 0.15,
        "availability": 0.05,
        # "historical": 0.15 — added by probability engine
    }

    def match(
        self,
        task: MetaTask,
        workers: List[RegisteredWorker],
        *,
        negative_filter: Optional[List[str]] = None,
    ) -> List[MatchResult]:
        """Score every worker against the task.  Returns sorted by overall_score desc."""
        results: List[MatchResult] = []
        task_caps = self._extract_task_capabilities(task)
        neg_set = set(negative_filter) if negative_filter else set()

        for worker in workers:
            result = self._score_worker(task, task_caps, worker, neg_set)
            results.append(result)

        results.sort(key=lambda r: r.overall_score, reverse=True)
        return results

    def _score_worker(
        self,
        task: MetaTask,
        task_caps: Set[str],
        worker: RegisteredWorker,
        neg_set: Set[str],
    ) -> MatchResult:
        """Score a single worker against a task."""
        result = MatchResult(
            worker_id=worker.worker_id,
            task_id=task.task_id,
        )

        # Check negative filters.
        if neg_set.intersection(set(worker.capabilities)):
            result.blocked = True
            result.block_reasons.append("negative_capability_match")
            return result

        # Check worker negative capabilities against task needs.
        if worker.negative_capabilities:
            task_reqs = set(task.metadata.get("required_capabilities", []))
            blocked = set(worker.negative_capabilities).intersection(task_reqs)
            if blocked:
                result.blocked = True
                result.block_reasons.append(f"worker_blocks: {blocked}")
                return result

        # Capability score.
        worker_caps = set(worker.capabilities)
        if task_caps:
            matched = task_caps.intersection(worker_caps)
            result.matched_capabilities = sorted(matched)
            result.missing_capabilities = sorted(task_caps - worker_caps)
            result.capability_score = len(matched) / max(1, len(task_caps))
        else:
            # No specific capabilities required — base score on having any.
            result.capability_score = 0.5 if worker_caps else 0.3

        # Specificity score — how well the worker's preferred types match.
        if task.task_type in worker.preferred_task_types:
            result.specificity_score = 1.0
        elif not worker.preferred_task_types:
            result.specificity_score = 0.6  # no preference = moderate
        else:
            result.specificity_score = 0.2

        # Risk score — lower risk tier = higher score.
        result.risk_score = max(0.0, 1.0 - (worker.max_risk_tier - 1) / 3.0)

        # Context score — can the worker handle the task's complexity?
        complexity_tokens = {
            Complexity.LOW: 2048,
            Complexity.MEDIUM: 4096,
            Complexity.HIGH: 8192,
            Complexity.CRITICAL: 16384,
        }
        required = complexity_tokens.get(task.complexity, 4096) if task.complexity is not None else 4096
        if worker.max_context_tokens >= required:
            result.context_score = 1.0
        else:
            result.context_score = worker.max_context_tokens / max(1, required)

        # Availability score.
        result.availability_score = 1.0 if worker.available else 0.0

        # Weighted overall.
        result.overall_score = (
            result.capability_score * self.WEIGHTS["capability"]
            + result.specificity_score * self.WEIGHTS["specificity"]
            + result.risk_score * self.WEIGHTS["risk"]
            + result.context_score * self.WEIGHTS["context"]
            + result.availability_score * self.WEIGHTS["availability"]
        )

        return result

    @staticmethod
    def _extract_task_capabilities(task: MetaTask) -> Set[str]:
        """Extract required capabilities from a task's metadata."""
        caps = set(task.metadata.get("required_capabilities", []))
        # Infer from task_type.
        type_caps = {
            "implementation": {"python", "code"},
            "analysis": {"research", "analysis"},
            "repair": {"debugging", "code"},
            "test": {"testing", "python"},
            "doc": {"documentation", "writing"},
        }
        if task.task_type in type_caps:
            caps.update(type_caps[task.task_type])
        return caps
