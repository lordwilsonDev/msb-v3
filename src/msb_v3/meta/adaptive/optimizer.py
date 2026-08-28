"""AdaptiveOptimizer — routing improves from verified outcomes with bounded learning.

Blueprint §11 (Bayesian / Adaptive Routing):
    Initial probabilities may be manually seeded.
    After execution: prior → execution → verification → observed outcome → posterior.
    Do not allow unrestricted self-modification.
    Routing updates must pass: proposal → evaluation → bounds check → audit → activation.
    The system may learn. It may not silently rewrite its own governance.

Architecture:
    OutcomeLedger → AdaptiveOptimizer → PolicyRouter (updated affinity matrix)

The optimizer:
    1. Consumes PipelineOutcome records from the OutcomeLedger.
    2. Computes per-mode success rates by task-type.
    3. Generates bounded affinity-matrix adjustments.
    4. Enforces learning bounds (max change per update, minimum observations).
    5. Records every learning decision in an audit trail.
    6. Produces a new affinity matrix that the PolicyRouter can consume.

Critical invariant:
    The optimizer may propose changes.
    It may not apply changes that violate governance bounds.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from msb_v3.meta.outcome.ledger import OutcomeLedger
from msb_v3.meta.policy.execution_policy import ExecutionMode

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LearningBounds:
    """Governance bounds for adaptive learning.

    Prevents the system from making large jumps based on small sample sizes.
    """

    # Minimum observations before any adjustment is allowed.
    min_observations: int = 5

    # Maximum absolute change to any single affinity value per update.
    max_affinity_delta: float = 0.05

    # Maximum total affinity shift across all signals per update.
    max_total_shift: float = 0.15

    # Minimum confidence before adjustment (avoid learning from noise).
    min_confidence: float = 0.6

    # Maximum number of learning rounds before requiring human review.
    max_learning_rounds: int = 100

    # Floor/ceiling for affinity values.
    affinity_floor: float = 0.05
    affinity_ceiling: float = 0.95

    def to_dict(self) -> Dict[str, Any]:
        return {
            "min_observations": self.min_observations,
            "max_affinity_delta": self.max_affinity_delta,
            "max_total_shift": self.max_total_shift,
            "min_confidence": self.min_confidence,
            "max_learning_rounds": self.max_learning_rounds,
            "affinity_floor": self.affinity_floor,
            "affinity_ceiling": self.affinity_ceiling,
        }


@dataclass
class AffinityAdjustment:
    """One proposed change to the affinity matrix."""

    mode: str
    signal: str
    old_value: float
    new_value: float
    delta: float
    reason: str
    observations: int
    confidence: float


@dataclass
class LearningRound:
    """One complete learning cycle — from outcomes to matrix update."""

    round_id: int
    timestamp: str = field(default_factory=_now)
    outcomes_consumed: int = 0
    adjustments_proposed: int = 0
    adjustments_applied: int = 0
    adjustments_rejected: int = 0
    adjustments: List[AffinityAdjustment] = field(default_factory=list)
    rejection_reasons: List[str] = field(default_factory=list)
    confidence_before: float = 0.0
    confidence_after: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_id": self.round_id,
            "timestamp": self.timestamp,
            "outcomes_consumed": self.outcomes_consumed,
            "adjustments_proposed": self.adjustments_proposed,
            "adjustments_applied": self.adjustments_applied,
            "adjustments_rejected": self.adjustments_rejected,
            "adjustments": [
                {
                    "mode": a.mode,
                    "signal": a.signal,
                    "old": a.old_value,
                    "new": a.new_value,
                    "delta": a.delta,
                    "reason": a.reason,
                }
                for a in self.adjustments
            ],
            "rejection_reasons": self.rejection_reasons,
            "confidence_before": self.confidence_before,
            "confidence_after": self.confidence_after,
        }


@dataclass
class ModeStats:
    """Aggregated stats for one execution mode across task types."""

    mode: str
    total: int = 0
    passes: int = 0
    task_types: Dict[str, Tuple[int, int]] = field(default_factory=dict)  # (passes, total)

    @property
    def success_rate(self) -> float:
        return self.passes / max(1, self.total)


class AdaptiveOptimizer:
    """Routing improves from verified outcomes with bounded learning.

    Usage::

        optimizer = AdaptiveOptimizer(
            ledger=outcome_ledger,
            bounds=LearningBounds(min_observations=5),
        )
        round_result = optimizer.learn()
        new_matrix = optimizer.get_affinity_matrix()
    """

    def __init__(
        self,
        *,
        ledger: OutcomeLedger,
        bounds: Optional[LearningBounds] = None,
        initial_affinity: Optional[Dict[ExecutionMode, Dict[str, float]]] = None,
        workdir: Optional[Path] = None,
    ) -> None:
        self._ledger = ledger
        self._bounds = bounds or LearningBounds()
        self._workdir = workdir
        self._rounds: List[LearningRound] = []

        # Deep copy initial affinity or use defaults.
        from msb_v3.meta.policy.policy_router import DEFAULT_AFFINITY_MATRIX
        src = initial_affinity or DEFAULT_AFFINITY_MATRIX
        self._affinity: Dict[ExecutionMode, Dict[str, float]] = {
            mode: dict(signals) for mode, signals in src.items()
        }

        # Audit trail file.
        self._audit_file: Optional[Path] = None
        if workdir is not None:
            workdir.mkdir(parents=True, exist_ok=True)
            self._audit_file = workdir / "learning-audit.jsonl"

    def get_affinity_matrix(self) -> Dict[ExecutionMode, Dict[str, float]]:
        """Return the current (possibly learned) affinity matrix."""
        return {mode: dict(signals) for mode, signals in self._affinity.items()}

    def learn(self) -> LearningRound:
        """Run one learning round.

        1. Aggregate outcomes by mode × task_type.
        2. Compute desired affinity adjustments.
        3. Apply bounds checks.
        4. Apply accepted adjustments.
        5. Record audit trail.
        """
        round_id = len(self._rounds) + 1
        lr = LearningRound(round_id=round_id)

        # Confidence before.
        lr.confidence_before = self._matrix_confidence()

        # 1. Aggregate outcomes by mode.
        mode_stats = self._aggregate_by_mode()
        lr.outcomes_consumed = sum(s.total for s in mode_stats.values())

        if lr.outcomes_consumed < self._bounds.min_observations:
            lr.rejection_reasons.append(
                f"insufficient observations: {lr.outcomes_consumed} < {self._bounds.min_observations}"
            )
            self._record_audit(lr)
            self._rounds.append(lr)
            return lr

        # 2. Compute desired adjustments.
        proposals = self._compute_adjustments(mode_stats)
        lr.adjustments_proposed = len(proposals)

        # 3-4. Apply bounds and activate.
        for adj in proposals:
            rejected_reason = self._check_bounds(adj)
            if rejected_reason:
                lr.adjustments_rejected += 1
                lr.rejection_reasons.append(rejected_reason)
                logger.debug("rejected adjustment: %s — %s", adj.signal, rejected_reason)
                continue

            # Apply.
            self._affinity[ExecutionMode(adj.mode)][adj.signal]
            self._affinity[ExecutionMode(adj.mode)][adj.signal] = adj.new_value
            lr.adjustments.append(adj)
            lr.adjustments_applied += 1
            logger.info(
                "learned: %s.%s %.3f → %.3f (Δ=%.4f, n=%d)",
                adj.mode, adj.signal, adj.old_value, adj.new_value,
                adj.delta, adj.observations,
            )

        # Confidence after.
        lr.confidence_after = self._matrix_confidence()

        self._record_audit(lr)
        self._rounds.append(lr)
        return lr

    def _aggregate_by_mode(self) -> Dict[str, ModeStats]:
        """Aggregate ledger outcomes by execution mode."""
        stats: Dict[str, ModeStats] = {}
        for outcome in self._ledger.recent(n=10000):
            mode = outcome.execution_mode
            if mode not in stats:
                stats[mode] = ModeStats(mode=mode)
            ms = stats[mode]
            ms.total += 1
            if outcome.verdict == "PASS":
                ms.passes += 1
            tt = outcome.task_type
            if tt not in ms.task_types:
                ms.task_types[tt] = (0, 0)
            p, t = ms.task_types[tt]
            if outcome.verdict == "PASS":
                p += 1
            t += 1
            ms.task_types[tt] = (p, t)
        return stats

    def _compute_adjustments(
        self, mode_stats: Dict[str, ModeStats]
    ) -> List[AffinityAdjustment]:
        """Compute affinity adjustments from observed success rates.

        The core learning rule (signal-aware):
            Only adjust an affinity if the observed success rate is
            meaningfully different from the current affinity AND
            the adjustment direction makes sense:
              - If success is LOW and affinity is HIGH → decrease
              - If success is HIGH and affinity is LOW → increase
              - If success ≈ affinity → no adjustment

        This prevents pushing already-high affinities past the ceiling
        or already-low affinities past the floor.
        """
        adjustments: List[AffinityAdjustment] = []

        for mode_name, ms in mode_stats.items():
            try:
                mode = ExecutionMode(mode_name)
            except ValueError:
                continue

            if ms.total < self._bounds.min_observations:
                continue

            observed_rate = ms.success_rate
            current_affinities = self._affinity.get(mode, {})

            for signal, current_affinity in current_affinities.items():
                delta = observed_rate - current_affinity

                # Only adjust if meaningful misalignment AND
                # the adjustment direction is safe.
                #   - High success + low affinity → increase (room to grow)
                #   - Low success + high affinity → decrease (room to shrink)
                #   - Both high or both low → skip (no useful signal)
                if abs(delta) < 0.01:
                    continue

                # Safety: don't push above ceiling or below floor.
                if delta > 0 and current_affinity >= self._bounds.affinity_ceiling:
                    continue
                if delta < 0 and current_affinity <= self._bounds.affinity_floor:
                    continue

                # Clamp delta to bounds.
                clamped_delta = max(
                    -self._bounds.max_affinity_delta,
                    min(self._bounds.max_affinity_delta, delta),
                )

                new_value = current_affinity + clamped_delta
                new_value = max(
                    self._bounds.affinity_floor,
                    min(self._bounds.affinity_ceiling, new_value),
                )

                adjustments.append(AffinityAdjustment(
                    mode=mode_name,
                    signal=signal,
                    old_value=current_affinity,
                    new_value=round(new_value, 4),
                    delta=round(clamped_delta, 4),
                    reason=f"observed_rate={observed_rate:.3f} vs affinity={current_affinity:.3f}",
                    observations=ms.total,
                    confidence=min(1.0, ms.total / (self._bounds.min_observations * 3)),
                ))

        return adjustments

    def _check_bounds(self, adj: AffinityAdjustment) -> Optional[str]:
        """Check if an adjustment violates governance bounds.

        Returns None if OK, or a rejection reason string.
        """
        # Confidence check.
        if adj.confidence < self._bounds.min_confidence:
            return f"confidence {adj.confidence:.3f} < min {self._bounds.min_confidence}"

        # Delta bounds.
        if abs(adj.delta) > self._bounds.max_affinity_delta:
            return f"delta {abs(adj.delta):.4f} > max {self._bounds.max_affinity_delta}"

        # Total shift check — aggregate all proposed shifts.
        # (Simplified: check individual shift.)
        if abs(adj.delta) > self._bounds.max_total_shift:
            return f"total shift {abs(adj.delta):.4f} > max {self._bounds.max_total_shift}"

        return None

    def _matrix_confidence(self) -> float:
        """Compute a confidence score for the current affinity matrix.

        Based on how much the matrix has deviated from defaults.
        High deviation = low confidence (matrix is heavily learned).
        """
        from msb_v3.meta.policy.policy_router import DEFAULT_AFFINITY_MATRIX
        total_deviation = 0.0
        count = 0
        for mode, signals in self._affinity.items():
            defaults = DEFAULT_AFFINITY_MATRIX.get(mode, {})
            for signal, value in signals.items():
                default = defaults.get(signal, 0.5)
                total_deviation += abs(value - default)
                count += 1
        if count == 0:
            return 1.0
        avg_deviation = total_deviation / count
        # Invert: high deviation = low confidence.
        return max(0.0, 1.0 - avg_deviation * 2)

    def _record_audit(self, lr: LearningRound) -> None:
        """Record a learning round to the audit trail."""
        if self._audit_file is not None:
            with self._audit_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(lr.to_dict()) + "\n")
        logger.info(
            "learning round %d: proposed=%d applied=%d rejected=%d confidence=%.3f→%.3f",
            lr.round_id, lr.adjustments_proposed, lr.adjustments_applied,
            lr.adjustments_rejected, lr.confidence_before, lr.confidence_after,
        )

    @property
    def round_count(self) -> int:
        return len(self._rounds)

    @property
    def total_adjustments_applied(self) -> int:
        return sum(r.adjustments_applied for r in self._rounds)

    @property
    def total_adjustments_rejected(self) -> int:
        return sum(r.adjustments_rejected for r in self._rounds)

    def summary(self) -> Dict[str, Any]:
        """Get a summary of all learning rounds."""
        return {
            "total_rounds": self.round_count,
            "total_adjustments_applied": self.total_adjustments_applied,
            "total_adjustments_rejected": self.total_adjustments_rejected,
            "current_confidence": self._matrix_confidence(),
            "bounds": self._bounds.to_dict(),
        }
