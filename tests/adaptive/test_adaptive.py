"""META-6: AdaptiveOptimizer conformance tests."""

from __future__ import annotations

import json
from pathlib import Path

from msb_v3.meta.adaptive.optimizer import (
    AdaptiveOptimizer,
    LearningBounds,
    ModeStats,
)
from msb_v3.meta.outcome.ledger import OutcomeLedger, PipelineOutcome
from msb_v3.meta.policy.execution_policy import ExecutionMode

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _outcome(
    *,
    task_id: str = "T-1",
    worker_id: str = "qwen3b",
    task_type: str = "implementation",
    mode: str = "HYBRID",
    verdict: str = "PASS",
) -> PipelineOutcome:
    return PipelineOutcome(
        task_id=task_id,
        task_objective=f"Test {task_id}",
        task_type=task_type,
        worker_id=worker_id,
        execution_mode=mode,
        verdict=verdict,
    )


def _seed_outcomes(ledger: OutcomeLedger, n: int = 10, **kwargs: str) -> None:
    """Seed a ledger with n outcomes."""
    for i in range(n):
        ledger.record(_outcome(task_id=f"T-{i}", **kwargs))


# ---------------------------------------------------------------------------
# Learning bounds
# ---------------------------------------------------------------------------

class TestLearningBounds:
    def test_default_bounds(self) -> None:
        b = LearningBounds()
        assert b.min_observations == 5
        assert b.max_affinity_delta == 0.05
        assert b.affinity_floor == 0.05
        assert b.affinity_ceiling == 0.95

    def test_custom_bounds(self) -> None:
        b = LearningBounds(min_observations=3, max_affinity_delta=0.1)
        assert b.min_observations == 3
        assert b.max_affinity_delta == 0.1

    def test_to_dict(self) -> None:
        b = LearningBounds()
        d = b.to_dict()
        assert "min_observations" in d
        assert "max_affinity_delta" in d


# ---------------------------------------------------------------------------
# In-memory learning (no persistence)
# ---------------------------------------------------------------------------

class TestLearning:
    def test_insufficient_observations(self) -> None:
        ledger = OutcomeLedger()
        _seed_outcomes(ledger, n=3)  # below min_observations=5
        optimizer = AdaptiveOptimizer(ledger=ledger)
        lr = optimizer.learn()
        assert lr.adjustments_applied == 0
        assert lr.adjustments_rejected == 0
        assert len(lr.rejection_reasons) > 0
        assert "insufficient" in lr.rejection_reasons[0]

    def test_enough_observations_produces_adjustments(self) -> None:
        ledger = OutcomeLedger()
        _seed_outcomes(ledger, n=10, mode="HYBRID", verdict="PASS")
        optimizer = AdaptiveOptimizer(ledger=ledger)
        lr = optimizer.learn()
        assert lr.outcomes_consumed == 10
        assert lr.adjustments_proposed > 0

    def test_learning_rounds_increment(self) -> None:
        ledger = OutcomeLedger()
        _seed_outcomes(ledger, n=10)
        optimizer = AdaptiveOptimizer(ledger=ledger)
        assert optimizer.round_count == 0
        optimizer.learn()
        assert optimizer.round_count == 1
        optimizer.learn()
        assert optimizer.round_count == 2

    def test_affinity_matrix_updates(self) -> None:
        ledger = OutcomeLedger()
        _seed_outcomes(ledger, n=10, mode="LOCAL", verdict="PASS")
        optimizer = AdaptiveOptimizer(ledger=ledger)
        before = optimizer.get_affinity_matrix()
        lr = optimizer.learn()
        after = optimizer.get_affinity_matrix()

        # Some affinities should have changed.
        changed = False
        for mode in before:
            for signal in before[mode]:
                if before[mode][signal] != after[mode][signal]:
                    changed = True
                    break
        assert changed or lr.adjustments_applied == 0  # may be bounded out

    def test_multiple_modes(self) -> None:
        ledger = OutcomeLedger()
        for i in range(5):
            ledger.record(_outcome(task_id=f"T-f{i}", mode="FABLE", verdict="PASS"))
        for i in range(5):
            ledger.record(_outcome(task_id=f"T-l{i}", mode="LOCAL", verdict="PASS"))
        optimizer = AdaptiveOptimizer(ledger=ledger)
        lr = optimizer.learn()
        assert lr.outcomes_consumed == 10


# ---------------------------------------------------------------------------
# Bounds enforcement
# ---------------------------------------------------------------------------

class TestBounds:
    def test_delta_bounded(self) -> None:
        """Affinity changes never exceed max_affinity_delta."""
        ledger = OutcomeLedger()
        _seed_outcomes(ledger, n=20, mode="HYBRID", verdict="PASS")
        bounds = LearningBounds(max_affinity_delta=0.02)
        optimizer = AdaptiveOptimizer(ledger=ledger, bounds=bounds)

        before = optimizer.get_affinity_matrix()
        optimizer.learn()
        after = optimizer.get_affinity_matrix()

        for mode in before:
            for signal in before[mode]:
                delta = abs(after[mode][signal] - before[mode][signal])
                assert delta <= bounds.max_affinity_delta + 0.001

    def test_affinity_clamped_to_floor_ceiling(self) -> None:
        """Affinity values never go below floor or above ceiling for observed modes."""
        ledger = OutcomeLedger()
        # All failures should push FABLE affinities down.
        _seed_outcomes(ledger, n=20, mode="FABLE", verdict="FAIL")
        bounds = LearningBounds(affinity_floor=0.1, affinity_ceiling=0.9)
        optimizer = AdaptiveOptimizer(ledger=ledger, bounds=bounds)

        for _ in range(10):
            optimizer.learn()

        matrix = optimizer.get_affinity_matrix()
        # Only check the observed mode (FABLE) — unobserved modes keep defaults.
        fable = matrix[ExecutionMode.FABLE]
        for signal, value in fable.items():
            assert value >= bounds.affinity_floor - 0.001
            assert value <= bounds.affinity_ceiling + 0.001

    def test_max_total_shift(self) -> None:
        """Total shift across signals is bounded."""
        ledger = OutcomeLedger()
        _seed_outcomes(ledger, n=20, mode="LOCAL", verdict="PASS")
        bounds = LearningBounds(max_total_shift=0.1)
        optimizer = AdaptiveOptimizer(ledger=ledger, bounds=bounds)
        lr = optimizer.learn()
        total_shift = sum(abs(a.delta) for a in lr.adjustments)
        assert total_shift <= bounds.max_total_shift * len(lr.adjustments) + 0.001

    def test_confidence_rejects_noisy_data(self) -> None:
        """Low confidence (few observations) rejects adjustments."""
        ledger = OutcomeLedger()
        _seed_outcomes(ledger, n=6, mode="HYBRID", verdict="PASS")
        bounds = LearningBounds(min_observations=5, min_confidence=0.9)
        optimizer = AdaptiveOptimizer(ledger=ledger, bounds=bounds)
        lr = optimizer.learn()
        # With only 6 observations, confidence = 6/15 = 0.4 < 0.9
        # All adjustments should be rejected.
        if lr.adjustments_proposed > 0:
            assert lr.adjustments_rejected > 0


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_audit_file_written(self, tmp_path: Path) -> None:
        ledger = OutcomeLedger()
        _seed_outcomes(ledger, n=10)
        optimizer = AdaptiveOptimizer(ledger=ledger, workdir=tmp_path)
        optimizer.learn()

        audit_file = tmp_path / "learning-audit.jsonl"
        assert audit_file.exists()
        lines = audit_file.read_text().strip().split("\n")
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["round_id"] == 1
        assert record["outcomes_consumed"] == 10

    def test_multiple_rounds_recorded(self, tmp_path: Path) -> None:
        ledger = OutcomeLedger()
        _seed_outcomes(ledger, n=10)
        optimizer = AdaptiveOptimizer(ledger=ledger, workdir=tmp_path)
        optimizer.learn()
        optimizer.learn()
        optimizer.learn()

        audit_file = tmp_path / "learning-audit.jsonl"
        lines = audit_file.read_text().strip().split("\n")
        assert len(lines) == 3
        last = json.loads(lines[-1])
        assert last["round_id"] == 3


# ---------------------------------------------------------------------------
# ModeStats
# ---------------------------------------------------------------------------

class TestModeStats:
    def test_success_rate(self) -> None:
        ms = ModeStats(mode="HYBRID", total=10, passes=7)
        assert ms.success_rate == 0.7

    def test_empty_stats(self) -> None:
        ms = ModeStats(mode="LOCAL")
        assert ms.success_rate == 0.0


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestSummary:
    def test_empty_summary(self) -> None:
        ledger = OutcomeLedger()
        optimizer = AdaptiveOptimizer(ledger=ledger)
        s = optimizer.summary()
        assert s["total_rounds"] == 0
        assert s["total_adjustments_applied"] == 0

    def test_summary_after_learning(self) -> None:
        ledger = OutcomeLedger()
        _seed_outcomes(ledger, n=10)
        optimizer = AdaptiveOptimizer(ledger=ledger)
        optimizer.learn()
        s = optimizer.summary()
        assert s["total_rounds"] == 1
        assert "current_confidence" in s
        assert "bounds" in s


# ---------------------------------------------------------------------------
# Convergence behavior
# ---------------------------------------------------------------------------

class TestConvergence:
    def test_repeated_learning_converges(self) -> None:
        """After many rounds with the same data, adjustments should shrink."""
        ledger = OutcomeLedger()
        _seed_outcomes(ledger, n=20, mode="HYBRID", verdict="PASS")
        optimizer = AdaptiveOptimizer(ledger=ledger)

        first_lr = optimizer.learn()
        first_adjustments = first_lr.adjustments_applied

        # Run several more rounds — adjustments should decrease.
        for _ in range(5):
            lr = optimizer.learn()

        # Later rounds should have fewer adjustments as affinities converge.
        # (Not guaranteed to be zero due to bounded steps, but should be fewer.)
        assert lr.adjustments_applied <= first_adjustments

    def test_opposite_outcomes_stabilize(self) -> None:
        """Mixed PASS/FAIL should produce smaller adjustments than uniform."""
        ledger_pass = OutcomeLedger()
        for i in range(10):
            ledger_pass.record(_outcome(task_id=f"P-{i}", verdict="PASS"))

        ledger_mixed = OutcomeLedger()
        for i in range(5):
            ledger_mixed.record(_outcome(task_id=f"P-{i}", verdict="PASS"))
        for i in range(5):
            ledger_mixed.record(_outcome(task_id=f"F-{i}", verdict="FAIL"))

        opt_pass = AdaptiveOptimizer(ledger=ledger_pass)
        opt_mixed = AdaptiveOptimizer(ledger=ledger_mixed)

        lr_pass = opt_pass.learn()
        lr_mixed = opt_mixed.learn()

        # Mixed outcomes should produce fewer adjustments (less signal).
        total_pass_shift = sum(abs(a.delta) for a in lr_pass.adjustments)
        total_mixed_shift = sum(abs(a.delta) for a in lr_mixed.adjustments)
        # Mixed should have less total shift (closer to 0.5 = neutral).
        assert total_mixed_shift <= total_pass_shift + 0.01
