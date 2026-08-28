"""PolicyRouter — deterministic scoring engine for execution mode selection.

The PolicyRouter answers: *Given this task's characteristics, which execution
regime has the highest expected probability of producing a verified result?*

It does NOT hard-code probabilities initially.  It uses a deterministic
scoring matrix based on task signals.  After execution, the Outcome Ledger
(META-10) feeds back to update the matrix (META-11/META-12).

Signals used for scoring:
    - complexity (from MetaTask.complexity)
    - task_type (implementation, analysis, repair, test, doc)
    - ambiguity (metadata flag or inferred from spec completeness)
    - verification availability (do we have tests/checks?)
    - cost sensitivity (metadata flag)
    - novelty (metadata flag or inferred from dependencies)
    - local capability (can a small model handle this class?)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from msb_v3.meta.contracts import Complexity, MetaTask
from msb_v3.meta.policy.execution_policy import (
    ExecutionMode,
    ExecutionPolicy,
)

logger = logging.getLogger(__name__)


@dataclass
class PolicyScores:
    """Raw signal scores extracted from a task — used for mode selection."""

    # Task signals (0.0–1.0).
    complexity: float = 0.5
    ambiguity: float = 0.3
    verification_available: float = 0.5
    cost_sensitivity: float = 0.5
    novelty: float = 0.3
    local_capability: float = 0.5
    has_dependencies: float = 0.0
    task_type_score: float = 0.5  # generic middle

    def to_dict(self) -> Dict[str, float]:
        return {
            "complexity": self.complexity,
            "ambiguity": self.ambiguity,
            "verification_available": self.verification_available,
            "cost_sensitivity": self.cost_sensitivity,
            "novelty": self.novelty,
            "local_capability": self.local_capability,
            "has_dependencies": self.has_dependencies,
            "task_type_score": self.task_type_score,
        }


# Default scoring matrix — deterministic, seedable, learnable.
# Rows: execution modes.  Columns: task signals.
# Each entry is the mode's *affinity* for that signal (0.0–1.0).
# High affinity = this mode is *good for* this signal value.
#
# Example: Fable has high affinity for complexity (0.95) and ambiguity (0.95)
# but low affinity for cost sensitivity (0.10).
DEFAULT_AFFINITY_MATRIX: Dict[ExecutionMode, Dict[str, float]] = {
    ExecutionMode.FABLE: {
        "complexity": 0.95,
        "ambiguity": 0.95,
        "verification_available": 0.40,
        "cost_sensitivity": 0.10,
        "novelty": 0.95,
        "local_capability": 0.20,
        "has_dependencies": 0.30,
        "task_type_score": 0.50,
    },
    ExecutionMode.HYBRID: {
        "complexity": 0.65,
        "ambiguity": 0.55,
        "verification_available": 0.90,
        "cost_sensitivity": 0.65,
        "novelty": 0.60,
        "local_capability": 0.90,
        "has_dependencies": 0.80,
        "task_type_score": 0.75,
    },
    ExecutionMode.LOCAL: {
        "complexity": 0.20,
        "ambiguity": 0.15,
        "verification_available": 0.98,
        "cost_sensitivity": 0.95,
        "novelty": 0.10,
        "local_capability": 0.95,
        "has_dependencies": 0.50,
        "task_type_score": 0.60,
    },
}

# Default weights for combining signals.
DEFAULT_SIGNAL_WEIGHTS: Dict[str, float] = {
    "complexity": 0.20,
    "ambiguity": 0.15,
    "verification_available": 0.15,
    "cost_sensitivity": 0.10,
    "novelty": 0.15,
    "local_capability": 0.15,
    "has_dependencies": 0.05,
    "task_type_score": 0.05,
}


class PolicyRouter:
    """Deterministic execution-mode selection from task characteristics.

    The router:
        1. Extracts signals from the task.
        2. Scores each execution mode against the signals.
        3. Selects the mode with highest expected utility.
        4. Produces a complete ExecutionPolicy with shape and assignments.

    Usage::

        router = PolicyRouter()
        policy = router.route(meta_task)
        # policy.mode == ExecutionMode.HYBRID
        # policy.shape == ExecutionShape(COMPILED, LIMITED, STRUCTURED, STRICT)
    """

    def __init__(
        self,
        *,
        affinity_matrix: Optional[Dict[ExecutionMode, Dict[str, float]]] = None,
        signal_weights: Optional[Dict[str, float]] = None,
        default_mode: ExecutionMode = ExecutionMode.HYBRID,
    ) -> None:
        self._affinity = affinity_matrix or DEFAULT_AFFINITY_MATRIX
        self._weights = signal_weights or DEFAULT_SIGNAL_WEIGHTS
        self._default_mode = default_mode

    def route(self, task: MetaTask) -> ExecutionPolicy:
        """Select the best execution mode for *task* and build a full policy."""
        scores = self._extract_signals(task)
        mode_scores = self._score_modes(scores)

        # Select best mode.
        sorted_modes = sorted(mode_scores.items(), key=lambda x: x[1], reverse=True)
        best_mode, best_score = sorted_modes[0]
        alternatives = [m.value for m, _ in sorted_modes[1:]]

        # Compute confidence — difference between best and second-best.
        second_score = sorted_modes[1][1] if len(sorted_modes) > 1 else 0.0
        confidence = min(1.0, max(0.0, best_score - second_score + 0.5))

        # Build the policy.
        policy = self._build_policy(task, best_mode, scores, confidence, alternatives)
        return policy

    def _extract_signals(self, task: MetaTask) -> PolicyScores:
        """Extract task signals into a normalized PolicyScores."""
        # Complexity signal.
        complexity_map = {
            Complexity.LOW: 0.2,
            Complexity.MEDIUM: 0.5,
            Complexity.HIGH: 0.8,
            Complexity.CRITICAL: 1.0,
        }
        complexity = complexity_map.get(task.complexity, 0.5) if task.complexity is not None else 0.5

        # Ambiguity — inferred from metadata or defaults.
        meta = task.metadata
        ambiguity = float(meta.get("ambiguity", 0.3))

        # Verification — do we have checks?
        ver_commands = task.metadata.get("verification_commands", [])
        has_tests = len(ver_commands) > 0
        verification = 0.9 if has_tests else 0.3

        # Cost sensitivity.
        cost_sensitive = float(meta.get("cost_sensitivity", 0.5))

        # Novelty — new code vs. modification.
        novelty = float(meta.get("novelty", 0.3))
        if not task.dependencies:
            novelty = max(novelty, 0.5)  # no deps = likely novel

        # Local capability — can a small model handle this?
        local_cap = 0.9 if task.task_type in ("implementation", "test", "repair") else 0.4

        # Dependencies.
        has_deps = 1.0 if task.dependencies else 0.0

        # Task type.
        task_type_map = {
            "implementation": 0.7,
            "analysis": 0.5,
            "repair": 0.6,
            "test": 0.8,
            "doc": 0.6,
            "research": 0.4,
        }
        task_type_score = task_type_map.get(task.task_type, 0.5)

        return PolicyScores(
            complexity=complexity,
            ambiguity=ambiguity,
            verification_available=verification,
            cost_sensitivity=cost_sensitive,
            novelty=novelty,
            local_capability=local_cap,
            has_dependencies=has_deps,
            task_type_score=task_type_score,
        )

    def _score_modes(self, scores: PolicyScores) -> Dict[ExecutionMode, float]:
        """Score each execution mode against the task signals."""
        signal_values = scores.to_dict()
        mode_totals: Dict[ExecutionMode, float] = {}

        for mode, affinities in self._affinity.items():
            total = 0.0
            weight_sum = 0.0
            for signal, weight in self._weights.items():
                signal_val = signal_values.get(signal, 0.5)
                affinity = affinities.get(signal, 0.5)
                # Affinity × signal value × weight.
                # High affinity for a high signal value = good match.
                total += affinity * signal_val * weight
                weight_sum += weight
            mode_totals[mode] = total / max(0.001, weight_sum)

        return mode_totals

    def _build_policy(
        self,
        task: MetaTask,
        mode: ExecutionMode,
        scores: PolicyScores,
        confidence: float,
        alternatives: List[str],
    ) -> ExecutionPolicy:
        """Build a complete ExecutionPolicy for the selected mode."""
        if mode is ExecutionMode.FABLE:
            preset = ExecutionPolicy.fable(task.task_id)
        elif mode is ExecutionMode.LOCAL:
            preset = ExecutionPolicy.local(task.task_id)
        else:
            preset = ExecutionPolicy.hybrid(task.task_id)

        # Override with task-specific adjustments.
        policy = ExecutionPolicy(
            task_id=task.task_id,
            mode=mode,
            shape=preset.shape,
            planner=preset.planner,
            executor=preset.executor,
            verifier=preset.verifier,
            parallelism=preset.parallelism,
            max_retries=preset.max_retries,
            escalation=preset.escalation,
            confidence=confidence,
            reason=f"mode={mode.value} score={confidence:.3f}",
            alternatives=alternatives,
            metadata={
                "signals": scores.to_dict(),
                "preset": mode.value,
            },
        )

        # Adjust retries based on cost sensitivity.
        if scores.cost_sensitivity > 0.8:
            policy.max_retries = min(policy.max_retries, 2)

        # Adjust parallelism based on complexity.
        if scores.complexity > 0.8:
            policy.parallelism = 1  # complex tasks need sequential focus

        # Set escalation chain.
        if mode is ExecutionMode.HYBRID:
            policy.escalation = ExecutionMode.FABLE
        elif mode is ExecutionMode.LOCAL:
            policy.escalation = ExecutionMode.HYBRID

        logger.info(
            "policy route: task=%s mode=%s confidence=%.3f",
            task.task_id, mode.value, confidence,
        )
        return policy
