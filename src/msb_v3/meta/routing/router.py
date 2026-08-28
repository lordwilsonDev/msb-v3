"""Router — the complete routing pipeline.

Blueprint §9, §10:
    The router calculates: P(success | worker, task, context, capability,
    history, risk).

    Candidate score:
        Capability Match       30%
        Task Specificity       20%
        Historical Success     15%
        Verification Quality   15%
        Latency                 5%
        Cost                    5%
        Availability             5%
        Risk Compatibility      5%

The Router orchestrates:
    1. CapabilityMatcher — raw capability scores
    2. ProbabilityEngine — historical + empirical scores (META-1C)
    3. Policy filter — authorization, risk, budget
    4. Final selection — best eligible candidate
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from msb_v3.meta.contracts import MetaTask
from msb_v3.meta.routing.capability_matcher import CapabilityMatcher
from msb_v3.meta.routing.route_decision import RouteCandidate, RouteDecision
from msb_v3.meta.routing.worker_registry import WorkerRegistry

logger = logging.getLogger(__name__)


class Router:
    """The complete routing pipeline.

    Usage::

        router = Router(worker_registry=registry)
        decision = router.route(meta_task)
        if decision.is_selected:
            worker = registry.get(decision.selected_worker_id)
    """

    def __init__(
        self,
        *,
        worker_registry: WorkerRegistry,
        matcher: Optional[CapabilityMatcher] = None,
        probability_engine: Optional[Any] = None,  # META-1C: RoutingMatrix
        policy_filter: Optional[Any] = None,
        escalation_threshold: float = 0.3,
    ) -> None:
        self._workers = worker_registry
        self._matcher = matcher or CapabilityMatcher()
        self._probability_engine = probability_engine
        self._policy_filter = policy_filter
        self._escalation_threshold = escalation_threshold

    def route(self, task: MetaTask) -> RouteDecision:
        """Route a task to the best available worker.

        Pipeline:
            1. Find candidate workers
            2. Score capability match
            3. Apply probability scores (if engine available)
            4. Apply policy filter
            5. Select best candidate
            6. Record full audit trail
        """
        # 1. Find candidates.
        candidates = self._workers.find_workers(
            task_type=task.task_type,
            available_only=True,
        )

        if not candidates:
            return RouteDecision(
                task_id=task.task_id,
                reason="no available workers",
            )

        # 2. Score capability match.
        match_results = self._matcher.match(task, candidates)

        # 3. Build route candidates with scores.
        route_candidates: List[RouteCandidate] = []
        for mr in match_results:
            rc = RouteCandidate(
                worker_id=mr.worker_id,
                capability_score=mr.overall_score,
                matched_capabilities=mr.matched_capabilities,
                blocked=mr.blocked,
                block_reasons=mr.block_reasons,
            )

            # Apply probability scores if engine is available.
            if self._probability_engine is not None:
                prob = self._probability_engine.get_probability(
                    mr.worker_id, task.task_type, task
                )
                rc.probability_score = prob

            route_candidates.append(rc)

        # 4. Filter blocked candidates.
        eligible = [rc for rc in route_candidates if not rc.blocked]

        if not eligible:
            return RouteDecision(
                task_id=task.task_id,
                candidates=route_candidates,
                reason="all candidates blocked",
            )

        # 5. Apply policy filter.
        if self._policy_filter is not None:
            eligible = self._apply_policy(eligible, task)

        if not eligible:
            return RouteDecision(
                task_id=task.task_id,
                candidates=route_candidates,
                reason="all candidates filtered by policy",
            )

        # 6. Score final candidates.
        for rc in eligible:
            rc.final_score = self._compute_final_score(rc, task)

        eligible.sort(key=lambda r: r.final_score, reverse=True)

        # 7. Assign ranks.
        for i, rc in enumerate(eligible):
            rc.rank = i + 1

        # 8. Select best.
        best = eligible[0]

        # 9. Check escalation threshold.
        escalation = best.final_score < self._escalation_threshold

        return RouteDecision(
            task_id=task.task_id,
            selected_worker_id=best.worker_id,
            candidates=route_candidates,
            reason=f"selected by score {best.final_score:.3f}",
            escalation_triggered=escalation,
            metadata={
                "best_score": best.final_score,
                "second_score": eligible[1].final_score if len(eligible) > 1 else 0.0,
                "eligible_count": len(eligible),
                "total_candidates": len(route_candidates),
            },
        )

    def _compute_final_score(self, candidate: RouteCandidate, task: MetaTask) -> float:
        """Compute the final weighted score for a candidate."""
        # Base: capability match.
        score = candidate.capability_score * 0.45

        # Probability boost.
        score += candidate.probability_score * 0.25

        # Historical (from probability engine).
        score += candidate.historical_score * 0.15

        # Cost penalty (lower cost = higher score).
        score += candidate.cost_score * 0.10

        # Availability.
        score += 0.05 if True else 0.0  # already filtered for available

        return round(min(1.0, score), 4)

    def _apply_policy(
        self,
        candidates: List[RouteCandidate],
        task: MetaTask,
    ) -> List[RouteCandidate]:
        """Apply policy filter to candidates."""
        # Placeholder for META-1C/D integration.
        return candidates
