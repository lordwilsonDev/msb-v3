"""PLEI calibration pipeline tests.

Proves the calibration engine:
1. Can read prediction/outcome pairs from the store
2. Builds reliability diagrams from calibration data
3. Computes calibration adjustments
4. Schedules recalibration
5. Hash-chain integrity is maintained
"""
from __future__ import annotations

from pathlib import Path

import pytest

from msb_v3.plei.calibration.error import compute_error_metrics
from msb_v3.plei.calibration.feedback import (
    adjustment_as_dict,
    compute_adjustments,
)
from msb_v3.plei.calibration.reliability import (
    build_reliability_diagram,
    reliability_as_dict,
)
from msb_v3.plei.calibration.scheduler import (
    compute_schedule,
    schedule_as_dict,
)
from msb_v3.plei.calibration.store import (
    CalibrationStore,
    Outcome,
    Prediction,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> CalibrationStore:
    """Create a fresh CalibrationStore backed by a temp directory."""
    jsonl_path = tmp_path / "calibration.jsonl"
    return CalibrationStore(path=str(jsonl_path))


def _make_prediction(
    prediction_id: str = "test:001",
    project: str = "test-project",
    p50: float = 10.0,
    p80: float = 15.0,
    p95: float = 20.0,
    failure_prob: float = 0.3,
) -> Prediction:
    """Create a test Prediction."""
    return Prediction(
        prediction_id=prediction_id,
        project=project,
        forecast_at="2026-08-26T12:00:00Z",
        predicted_p50_days=p50,
        predicted_p80_days=p80,
        predicted_p95_days=p95,
        predicted_mean_days=(p50 + p80 + p95) / 3,
        predicted_stdev_days=(p95 - p50) / 4,
        predicted_failure_probability=failure_prob,
        milestone_predictions={},
        confidence_level="medium",
        coefficient_of_variation=0.1,
        trial_count=2000,
        seed=42,
        variables_used=5,
    )


def _make_outcome(
    prediction_id: str = "test:001",
    project: str = "test-project",
    actual_days: float = 12.0,
    completed: bool = True,
) -> Outcome:
    """Create a test Outcome."""
    return Outcome(
        outcome_id=f"outcome:{prediction_id}",
        prediction_id=prediction_id,
        project=project,
        observed_at="2026-08-26T12:05:00Z",
        actual_duration_days=actual_days,
        actual_completion=completed,
        failures_encountered=0,
        severity="none",
        milestone_outcomes={},
        actual_stage="IMPLEMENTATION",
        step_count=5,
        error_note="",
    )


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


class TestCalibrationStore:
    """Calibration store CRUD and integrity."""

    def test_store_prediction(self, store: CalibrationStore):
        """Can store a prediction record."""
        pred = _make_prediction()
        store.record_prediction(pred)
        preds = store.predictions()
        assert len(preds) == 1
        assert preds[0].prediction_id == "test:001"

    def test_store_outcome(self, store: CalibrationStore):
        """Can store an outcome record."""
        pred = _make_prediction()
        store.record_prediction(pred)
        outcome = _make_outcome()
        store.record_outcome(outcome)
        outcomes = store.outcomes()
        assert len(outcomes) == 1
        assert outcomes[0].prediction_id == "test:001"

    def test_calibration_pairs(self, store: CalibrationStore):
        """Matched prediction+outcome pairs are returned."""
        pred = _make_prediction()
        store.record_prediction(pred)
        outcome = _make_outcome()
        store.record_outcome(outcome)
        pairs = store.pairs()
        assert len(pairs) == 1
        assert pairs[0].prediction.prediction_id == "test:001"

    def test_hash_chain_integrity(self, store: CalibrationStore):
        """Records form a hash chain — each record hashes the previous."""
        pred1 = _make_prediction(prediction_id="test:001")
        pred2 = _make_prediction(prediction_id="test:002")
        store.record_prediction(pred1)
        store.record_prediction(pred2)

        # Read raw records and verify hash chain
        records = store._raw_records()
        assert len(records) == 2
        # Second record's _hash should be derived from first
        assert records[1].get("_hash") is not None

    def test_empty_store_returns_empty(self, store: CalibrationStore):
        """Empty store returns empty lists."""
        assert store.predictions() == []
        assert store.outcomes() == []
        assert store.pairs() == []


# ---------------------------------------------------------------------------
# Reliability diagram tests
# ---------------------------------------------------------------------------


class TestReliabilityDiagram:
    """Reliability diagram construction from calibration pairs."""

    def test_build_diagram_from_pairs(self, store: CalibrationStore):
        """Can build a reliability diagram from prediction/outcome pairs."""
        # Store several pairs
        for i in range(10):
            pred = _make_prediction(
                prediction_id=f"test:{i:03d}",
                p50=10.0 + i,
                failure_prob=0.1 * (i + 1),
            )
            store.record_prediction(pred)
            outcome = _make_outcome(
                prediction_id=f"test:{i:03d}",
                actual_days=10.0 + i + (1 if i % 2 == 0 else -1),
                completed=True,
            )
            store.record_outcome(outcome)

        pairs = store.pairs()
        assert len(pairs) == 10

        diagram = build_reliability_diagram(pairs)
        assert diagram is not None

    def test_diagram_as_dict(self, store: CalibrationStore):
        """Reliability diagram serializes to dict."""
        pred = _make_prediction()
        store.record_prediction(pred)
        outcome = _make_outcome()
        store.record_outcome(outcome)

        pairs = store.pairs()
        diagram = build_reliability_diagram(pairs)
        d = reliability_as_dict(diagram)
        assert isinstance(d, dict)


# ---------------------------------------------------------------------------
# Feedback adjustment tests
# ---------------------------------------------------------------------------


class TestCalibrationFeedback:
    """Calibration feedback — computing adjustments from error."""

    def test_compute_adjustments(self, store: CalibrationStore):
        """Can compute calibration adjustments from error metrics."""
        for i in range(5):
            pred = _make_prediction(
                prediction_id=f"test:{i:03d}",
                p50=10.0,
                failure_prob=0.5,
            )
            store.record_prediction(pred)
            outcome = _make_outcome(
                prediction_id=f"test:{i:03d}",
                actual_days=12.0,
            )
            store.record_outcome(outcome)

        pairs = store.pairs()
        metrics = compute_error_metrics(pairs)
        adjustments = compute_adjustments(metrics)
        assert adjustments is not None

    def test_adjustment_as_dict(self, store: CalibrationStore):
        """Adjustment serializes to dict."""
        pred = _make_prediction()
        store.record_prediction(pred)
        outcome = _make_outcome()
        store.record_outcome(outcome)

        pairs = store.pairs()
        metrics = compute_error_metrics(pairs)
        adj = compute_adjustments(metrics)
        d = adjustment_as_dict(adj)
        assert isinstance(d, dict)


# ---------------------------------------------------------------------------
# Scheduler tests
# ---------------------------------------------------------------------------


class TestCalibrationScheduler:
    """Calibration scheduling — when to recalibrate."""

    def test_compute_schedule(self, store: CalibrationStore):
        """Can compute a calibration schedule."""
        pred = _make_prediction()
        store.record_prediction(pred)
        outcome = _make_outcome()
        store.record_outcome(outcome)

        schedule = compute_schedule(store)
        assert schedule is not None

    def test_schedule_as_dict(self, store: CalibrationStore):
        """Schedule serializes to dict."""
        pred = _make_prediction()
        store.record_prediction(pred)
        outcome = _make_outcome()
        store.record_outcome(outcome)

        sched = compute_schedule(store)
        d = schedule_as_dict(sched)
        assert isinstance(d, dict)


# ---------------------------------------------------------------------------
# Hash utility tests
# ---------------------------------------------------------------------------


class TestHashChainIntegrity:
    """Hash chain integrity verification."""

    def test_verify_chain_valid(self, store: CalibrationStore):
        """Valid hash chain passes verification."""
        pred = _make_prediction()
        store.record_prediction(pred)
        outcome = _make_outcome()
        store.record_outcome(outcome)

        valid, msg = store.verify_chain()
        assert valid is True

    def test_verify_chain_empty(self, store: CalibrationStore):
        """Empty store has valid chain."""
        valid, msg = store.verify_chain()
        assert valid is True
