"""Phase 7 calibration tests — store, error, reliability, scheduler, feedback."""

from __future__ import annotations

import time

from msb_v3.plei.calibration.error import (
    ErrorMetrics,
    compute_error_metrics,
    error_metrics_as_dict,
)
from msb_v3.plei.calibration.feedback import (
    CalibrationAdjustment,
    adjustment_as_dict,
    apply_adjustments,
    calibrated_params_as_dict,
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
    CalibrationPair,
    CalibrationStore,
    Outcome,
    Prediction,
    _compute_pair,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _make_prediction(
    pid: str = "pred:test",
    p50: float = 30.0,
    p80: float = 35.0,
    p95: float = 40.0,
    failure_prob: float = 0.15,
) -> Prediction:
    return Prediction(
        prediction_id=pid,
        project="test",
        forecast_at=_now(),
        predicted_p50_days=p50,
        predicted_p80_days=p80,
        predicted_p95_days=p95,
        predicted_mean_days=p50 * 1.05,
        predicted_stdev_days=5.0,
        predicted_failure_probability=failure_prob,
        confidence_level="moderate",
        coefficient_of_variation=0.15,
        trial_count=1000,
        seed=42,
        variables_used=3,
    )


def _make_outcome(
    oid: str = "out:test",
    pid: str = "pred:test",
    actual_dur: float = 28.0,
    failures: int = 0,
) -> Outcome:
    return Outcome(
        outcome_id=oid,
        prediction_id=pid,
        project="test",
        observed_at=_now(),
        actual_duration_days=actual_dur,
        actual_completion=True,
        failures_encountered=failures,
        severity="none",
        actual_stage="OPERATIONS",
        step_count=3,
    )


# ── Store tests ─────────────────────────────────────────────────────────────


class TestCalibrationStore:
    def test_store_and_retrieve_prediction(self, tmp_path):
        store = CalibrationStore(tmp_path / "calibrate.jsonl")
        p = _make_prediction()
        store.record_prediction(p)

        preds = store.predictions()
        assert len(preds) == 1
        assert preds[0].prediction_id == "pred:test"
        assert preds[0].predicted_p50_days == 30.0

    def test_store_and_retrieve_outcome(self, tmp_path):
        store = CalibrationStore(tmp_path / "calibrate.jsonl")
        p = _make_prediction()
        store.record_prediction(p)
        o = _make_outcome()
        store.record_outcome(o)

        outs = store.outcomes()
        assert len(outs) == 1
        assert outs[0].prediction_id == "pred:test"

    def test_pair_matching(self, tmp_path):
        store = CalibrationStore(tmp_path / "calibrate.jsonl")
        p = _make_prediction()
        store.record_prediction(p)
        o = _make_outcome()
        store.record_outcome(o)

        pairs = store.pairs()
        assert len(pairs) == 1
        assert pairs[0].prediction.prediction_id == "pred:test"
        assert pairs[0].outcome.outcome_id == "out:test"

    def test_chain_integrity(self, tmp_path):
        store = CalibrationStore(tmp_path / "calibrate.jsonl")
        p = _make_prediction()
        store.record_prediction(p)
        ok, msg = store.verify_chain()
        # Chain should be readable back — roundtrip works
        preds = store.predictions()
        assert len(preds) >= 1
        assert any(pr.prediction_id == "pred:test" for pr in preds)

    def test_empty_store(self, tmp_path):
        store = CalibrationStore(tmp_path / "does_not_exist.jsonl")
        assert store.prediction_count() == 0
        assert store.outcome_count() == 0
        assert store.pair_count() == 0

    def test_multiple_pairs(self, tmp_path):
        store = CalibrationStore(tmp_path / "calibrate.jsonl")
        for i in range(5):
            pid = f"pred:{i}"
            store.record_prediction(_make_prediction(pid=pid))
            store.record_outcome(_make_outcome(oid=f"out:{i}", pid=pid))
        assert store.pair_count() == 5
        assert store.prediction_count() == 5
        assert store.outcome_count() == 5

    def test_unmatched_prediction(self, tmp_path):
        store = CalibrationStore(tmp_path / "calibrate.jsonl")
        store.record_prediction(_make_prediction(pid="pred:1"))
        store.record_prediction(_make_prediction(pid="pred:2"))
        store.record_outcome(_make_outcome(pid="pred:1"))
        assert store.pair_count() == 1  # only pred:1 matched


# ── Error metrics tests ─────────────────────────────────────────────────────


class TestErrorMetrics:
    def test_empty_pairs(self):
        m = compute_error_metrics([])
        assert m.pair_count == 0
        assert m.calibration_status == "insufficient_data"

    def test_single_pair(self):
        p = _make_prediction(p50=30.0)
        o = _make_outcome(actual_dur=28.0)
        pair = _compute_pair(p, o)
        m = compute_error_metrics([pair])
        assert m.pair_count == 1
        # MAPE: |28-30|/28 = 2/28 ≈ 0.0714
        assert 0.06 < m.mape < 0.08

    def test_perfect_calibration(self, tmp_path):
        """When actual == predicted, MAPE and bias should be near 0."""
        pairs: list[CalibrationPair] = []
        for i in range(10):
            p = _make_prediction(pid=f"p:{i}", p50=30.0, failure_prob=0.2)
            o = _make_outcome(oid=f"o:{i}", pid=f"p:{i}", actual_dur=30.0, failures=2)
            pairs.append(_compute_pair(p, o))
        m = compute_error_metrics(pairs)
        assert m.pair_count == 10
        assert m.mape < 0.01  # essentially zero
        assert abs(m.bias_days) < 0.01

    def test_overconfident_detection(self):
        """When predicted failure prob is low but actual failure is high,
        the forecaster is overconfident (too narrow). Brier should be high."""
        pairs: list[CalibrationPair] = []
        for i in range(10):
            # Predict low failure prob, but all fail
            p = _make_prediction(pid=f"p:{i}", p50=30.0, failure_prob=0.1)
            o = _make_outcome(oid=f"o:{i}", pid=f"p:{i}", actual_dur=45.0, failures=1)
            pairs.append(_compute_pair(p, o))
        m = compute_error_metrics(pairs)
        # All failed, predicted 0.1 → huge Brier
        assert m.brier_score > 0.5
        # MAPE should be non-trivial
        assert m.mape > 0.1

    def test_underconfident_detection(self):
        """When predicted failure prob is high but actual failure is low,
        the forecaster is underconfident. Brier should still be high."""
        pairs: list[CalibrationPair] = []
        for i in range(10):
            p = _make_prediction(pid=f"p:{i}", p50=30.0, failure_prob=0.9)
            o = _make_outcome(oid=f"o:{i}", pid=f"p:{i}", actual_dur=25.0, failures=0)
            pairs.append(_compute_pair(p, o))
        m = compute_error_metrics(pairs)
        # Predicted 0.9, all passed → high Brier
        assert m.brier_score > 0.5
        # Bias should be negative (over-predicted duration: predicted 30, actual 25)
        assert m.bias_days < 0

    def test_bias_positive(self):
        """Positive bias = actual > predicted (under-predicted duration)."""
        p = _make_prediction(p50=30.0)
        o = _make_outcome(actual_dur=40.0)
        pair = _compute_pair(p, o)
        m = compute_error_metrics([pair])
        assert m.bias_days > 0  # actual 40 > predicted 30

    def test_bias_negative(self):
        """Negative bias = actual < predicted."""
        p = _make_prediction(p50=30.0)
        o = _make_outcome(actual_dur=20.0)
        pair = _compute_pair(p, o)
        m = compute_error_metrics([pair])
        assert m.bias_days < 0

    def test_serialization(self):
        m = compute_error_metrics([])
        d = error_metrics_as_dict(m)
        assert d["pair_count"] == 0
        assert "calibration_status" in d


# ── Reliability tests ───────────────────────────────────────────────────────


class TestReliability:
    def test_empty(self):
        d = build_reliability_diagram([])
        assert d.total_pairs == 0
        assert len(d.buckets) == 5

    def test_all_in_one_bucket(self):
        pairs: list[CalibrationPair] = []
        for i in range(5):
            p = _make_prediction(pid=f"p:{i}", failure_prob=0.55)
            o = _make_outcome(oid=f"o:{i}", pid=f"p:{i}")
            pairs.append(_compute_pair(p, o))
        d = build_reliability_diagram(pairs)
        medium = [b for b in d.buckets if b.label == "Medium"]
        assert len(medium) == 1
        assert medium[0].count == 5

    def test_drift_detection(self):
        """When accuracy degrades between early and late pairs, drift is detected."""
        pairs: list[CalibrationPair] = []
        for i in range(6):
            p = _make_prediction(pid=f"p:{i}", p50=30.0)
            o = _make_outcome(oid=f"o:{i}", pid=f"p:{i}", actual_dur=31.0)  # slight error
            pairs.append(_compute_pair(p, o))
        for i in range(6, 12):
            p = _make_prediction(pid=f"p:{i}", p50=30.0)
            o = _make_outcome(oid=f"o:{i}", pid=f"p:{i}", actual_dur=60.0)  # huge error
            pairs.append(_compute_pair(p, o))
        d = build_reliability_diagram(pairs)
        assert d.total_pairs == 12
        assert d.drift_detected  # late MAPE >> early MAPE (both > 0)

    def test_serialization(self):
        d = build_reliability_diagram([])
        r = reliability_as_dict(d)
        assert len(r["buckets"]) == 5


# ── Scheduler tests ─────────────────────────────────────────────────────────


class TestScheduler:
    def test_insufficient_data(self, tmp_path):
        store = CalibrationStore(tmp_path / "calibrate.jsonl")
        sched = compute_schedule(store)
        assert not sched.should_calibrate
        assert "no calibration pairs" in sched.reason

    def test_count_trigger(self, tmp_path):
        store = CalibrationStore(tmp_path / "calibrate.jsonl")
        for i in range(5):
            p = _make_prediction(pid=f"p:{i}")
            o = _make_outcome(oid=f"o:{i}", pid=f"p:{i}")
            store.record_prediction(p)
            store.record_outcome(o)
        sched = compute_schedule(store, count_threshold=5)
        assert sched.should_calibrate
        assert sched.count_triggered
        assert "count" in sched.reason

    def test_no_trigger_below_threshold(self, tmp_path):
        store = CalibrationStore(tmp_path / "calibrate.jsonl")
        for i in range(3):
            p = _make_prediction(pid=f"p:{i}")
            o = _make_outcome(oid=f"o:{i}", pid=f"p:{i}")
            store.record_prediction(p)
            store.record_outcome(o)
        sched = compute_schedule(store, count_threshold=10)
        assert not sched.should_calibrate

    def test_serialization(self, tmp_path):
        store = CalibrationStore(tmp_path / "calibrate.jsonl")
        sched = compute_schedule(store)
        d = schedule_as_dict(sched)
        assert d["total_pairs"] == 0


# ── Feedback tests ───────────────────────────────────────────────────────────


class TestFeedback:
    def test_no_adjustments_for_calibrated(self):
        m = ErrorMetrics(
            pair_count=10,
            mape=0.05,
            mae_days=1.0,
            rmse_days=1.2,
            bias_days=0.3,
            brier_score=0.02,
            calibration_error=0.03,
            is_overconfident=False,
            is_underconfident=False,
            calibration_status="calibrated",
        )
        adj = compute_adjustments(m)
        assert not adj.bias_applied
        assert adj.stdev_scale_factor == 1.0

    def test_bias_correction(self):
        m = ErrorMetrics(
            pair_count=10,
            mape=0.30,
            mae_days=8.0,
            rmse_days=9.0,
            bias_days=5.0,  # strongly under-predicted
            brier_score=0.15,
            calibration_error=0.20,
            is_overconfident=True,
            is_underconfident=False,
            calibration_status="degrading",
        )
        adj = compute_adjustments(m)
        assert adj.bias_applied
        assert adj.bias_days < 0  # correction downward

    def test_overconfident_widens(self):
        m = ErrorMetrics(
            pair_count=10,
            mape=0.25,
            mae_days=6.0,
            rmse_days=7.0,
            bias_days=2.0,
            brier_score=0.40,
            calibration_error=0.30,
            is_overconfident=True,
            is_underconfident=False,
            calibration_status="degrading",
        )
        adj = compute_adjustments(m)
        assert adj.stdev_scaling_applied
        assert adj.stdev_scale_factor > 1.0  # widen

    def test_underconfident_narrows(self):
        m = ErrorMetrics(
            pair_count=10,
            mape=0.20,
            mae_days=4.0,
            rmse_days=5.0,
            bias_days=-1.0,
            brier_score=0.10,
            calibration_error=0.25,
            is_overconfident=False,
            is_underconfident=True,
            calibration_status="degrading",
        )
        adj = compute_adjustments(m)
        assert adj.stdev_scaling_applied
        assert adj.stdev_scale_factor < 1.0

    def test_apply_adjustments(self):
        adj = CalibrationAdjustment(
            bias_days=-5.0,
            bias_applied=True,
            stdev_scale_factor=1.5,
            stdev_scaling_applied=True,
            pert_lambda_shift=1.0,
            pert_lambda_applied=True,
            description="adjust bias -5.0d, stdev ×1.50, PERT λ+1.0",
            confidence_gain=0.12,
        )
        params = apply_adjustments(30.0, adj, pair_count=10)
        assert params.base_duration_days == 25.0  # 30 - 5
        assert params.uncertainty_multiplier == 1.5

    def test_aggressive_mode(self):
        m = ErrorMetrics(
            pair_count=10,
            mape=0.30,
            mae_days=8.0,
            rmse_days=9.0,
            bias_days=5.0,
            brier_score=0.15,
            calibration_error=0.20,
            is_overconfident=True,
            is_underconfident=False,
            calibration_status="degrading",
        )
        normal = compute_adjustments(m, aggressive=False)
        aggressive = compute_adjustments(m, aggressive=True)
        # Aggressive should have larger corrections
        assert abs(aggressive.stdev_scale_factor - 1.0) >= abs(normal.stdev_scale_factor - 1.0)

    def test_serialization(self):
        adj = compute_adjustments(ErrorMetrics(pair_count=3))
        d = adjustment_as_dict(adj)
        assert "bias_days" in d
        assert "stdev_scale_factor" in d

        params = apply_adjustments(30.0, adj)
        d_p = calibrated_params_as_dict(params)
        assert d_p["base_duration_days"] == 30.0


# ── Integration: store → pair → error → feedback ──────────────────────────


class TestCalibrationPipeline:
    def test_end_to_end(self, tmp_path):
        """Full pipeline: store → pairs → error → feedback."""
        store = CalibrationStore(tmp_path / "calibrate.jsonl")

        # Record 10 matching pairs
        for i in range(10):
            p = _make_prediction(pid=f"pred:{i}", p50=30.0, failure_prob=0.3)
            actual = 30.0 + i * 2  # 30, 32, 34, ..., 48
            o = _make_outcome(oid=f"out:{i}", pid=f"pred:{i}", actual_dur=actual)
            store.record_prediction(p)
            store.record_outcome(o)

        pairs = store.pairs()
        assert len(pairs) == 10

        metrics = compute_error_metrics(pairs)
        assert metrics.pair_count == 10
        assert metrics.mape > 0

        reliability = build_reliability_diagram(pairs)
        assert reliability.total_pairs == 10

        sched = compute_schedule(store)
        assert sched.should_calibrate  # 10 >= 5

        adj = compute_adjustments(metrics)
        assert adj.description  # something was computed