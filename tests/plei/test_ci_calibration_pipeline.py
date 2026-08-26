"""Tests for the PLEI CI calibration pipeline."""

from __future__ import annotations

import pytest

from msb_v3.plei.calibration.ci_pipeline import (
    CIPipeline,
)


@pytest.fixture()
def pipe(tmp_path: object) -> CIPipeline:
    return CIPipeline(path=str(tmp_path / "ci-calibration.jsonl"))


# ── Prediction recording ──────────────────────────────────────────────────


def test_record_prediction_returns_populated_id(pipe: CIPipeline) -> None:
    pred = pipe.record_prediction(
        expected_test_count=100,
        expected_pass_rate=0.95,
        expected_duration_seconds=60,
    )
    assert pred.prediction_id.startswith("ci:msb-v3:")
    assert pred.calibration_status == "pending"
    assert pred.expected_test_count == 100


def test_record_prediction_appends_to_file(pipe: CIPipeline) -> None:
    pipe.record_prediction(expected_test_count=50, expected_pass_rate=1.0, expected_duration_seconds=30)
    records = pipe._raw_records()
    assert len(records) == 1
    assert records[0]["type"] == "CI_PREDICTION"


def test_predictions_returns_all(pipe: CIPipeline) -> None:
    pipe.record_prediction(expected_test_count=10, expected_pass_rate=1.0, expected_duration_seconds=10)
    pipe.record_prediction(expected_test_count=20, expected_pass_rate=0.9, expected_duration_seconds=20)
    preds = pipe.predictions()
    assert len(preds) == 2
    assert preds[0].expected_test_count == 10
    assert preds[1].expected_test_count == 20


# ── Outcome recording ─────────────────────────────────────────────────────


def test_record_outcome(pipe: CIPipeline) -> None:
    pred = pipe.record_prediction(
        expected_test_count=100,
        expected_pass_rate=1.0,
        expected_duration_seconds=60,
    )
    out = pipe.record_outcome(
        prediction_id=pred.prediction_id,
        actual_test_count=98,
        actual_pass_rate=0.98,
        actual_duration_seconds=65,
    )
    assert out.outcome_id.startswith("ci-out:")
    assert out.actual_test_count == 98


def test_outcomes_returns_all(pipe: CIPipeline) -> None:
    pred = pipe.record_prediction(
        expected_test_count=100,
        expected_pass_rate=1.0,
        expected_duration_seconds=60,
    )
    pipe.record_outcome(
        prediction_id=pred.prediction_id,
        actual_test_count=98,
        actual_pass_rate=0.98,
        actual_duration_seconds=65,
    )
    outs = pipe.outcomes()
    assert len(outs) == 1
    assert outs[0].actual_test_count == 98


# ── Pairing ───────────────────────────────────────────────────────────────


def test_pairs_match_prediction_to_outcome(pipe: CIPipeline) -> None:
    pred = pipe.record_prediction(
        expected_test_count=100,
        expected_pass_rate=1.0,
        expected_duration_seconds=60,
    )
    pipe.record_outcome(
        prediction_id=pred.prediction_id,
        actual_test_count=100,
        actual_pass_rate=1.0,
        actual_duration_seconds=60,
    )
    pairs = pipe.pairs()
    assert len(pairs) == 1
    assert pairs[0].prediction_id == pred.prediction_id
    assert pairs[0].test_count_error == 0.0
    assert pairs[0].pass_rate_error == 0.0
    assert pairs[0].duration_error_pct == 0.0


def test_pairs_compute_error_metrics(pipe: CIPipeline) -> None:
    pred = pipe.record_prediction(
        expected_test_count=100,
        expected_pass_rate=1.0,
        expected_duration_seconds=100,
    )
    pipe.record_outcome(
        prediction_id=pred.prediction_id,
        actual_test_count=90,
        actual_pass_rate=0.95,
        actual_duration_seconds=120,
    )
    pair = pipe.pairs()[0]
    assert pair.test_count_error == pytest.approx(-0.1, abs=0.01)
    assert pair.pass_rate_error == pytest.approx(-0.05, abs=0.01)
    assert pair.duration_error_pct == pytest.approx(0.2, abs=0.01)


def test_pairs_unmatched_predictions_not_paired(pipe: CIPipeline) -> None:
    pipe.record_prediction(expected_test_count=100, expected_pass_rate=1.0, expected_duration_seconds=60)
    assert len(pipe.pairs()) == 0


def test_pairs_unmatched_outcomes_not_paired(pipe: CIPipeline) -> None:
    pipe.record_prediction(
        expected_test_count=100,
        expected_pass_rate=1.0,
        expected_duration_seconds=60,
    )
    pipe.record_outcome(
        prediction_id="nonexistent",
        actual_test_count=98,
        actual_pass_rate=0.98,
        actual_duration_seconds=65,
    )
    assert len(pipe.pairs()) == 0


# ── Calibration report ───────────────────────────────────────────────────


def test_report_insufficient_data(pipe: CIPipeline) -> None:
    report = pipe.calibration_report()
    assert report.verdict == "insufficient_data"
    assert report.is_calibrated is False


def test_report_well_calibrated(pipe: CIPipeline) -> None:
    for i in range(5):
        pred = pipe.record_prediction(
            expected_test_count=100,
            expected_pass_rate=1.0,
            expected_duration_seconds=60,
        )
        pipe.record_outcome(
            prediction_id=pred.prediction_id,
            actual_test_count=100 + (i % 2),  # tiny error
            actual_pass_rate=1.0,
            actual_duration_seconds=61,
        )
    report = pipe.calibration_report()
    assert report.pair_count == 5
    assert report.is_calibrated is True
    assert report.verdict in ("well_calibrated", "approximately_calibrated")


def test_report_miscalibrated(pipe: CIPipeline) -> None:
    for _ in range(5):
        pred = pipe.record_prediction(
            expected_test_count=100,
            expected_pass_rate=1.0,
            expected_duration_seconds=60,
        )
        pipe.record_outcome(
            prediction_id=pred.prediction_id,
            actual_test_count=50,  # huge error
            actual_pass_rate=0.5,
            actual_duration_seconds=200,
        )
    report = pipe.calibration_report()
    assert report.verdict == "miscalibrated"
    assert report.is_calibrated is False


def test_report_failure_accuracy(pipe: CIPipeline) -> None:
    # Predict failures that happen
    for _ in range(3):
        pred = pipe.record_prediction(
            expected_test_count=100,
            expected_pass_rate=1.0,
            expected_duration_seconds=60,
            expected_failures=1,
        )
        pipe.record_outcome(
            prediction_id=pred.prediction_id,
            actual_test_count=98,
            actual_pass_rate=0.98,
            actual_duration_seconds=65,
            actual_failures=1,
        )
    report = pipe.calibration_report()
    assert report.failure_accuracy == 1.0


# ── Hash chain ────────────────────────────────────────────────────────────


def test_chain_integrity_clean(pipe: CIPipeline) -> None:
    for _ in range(10):
        pred = pipe.record_prediction(
            expected_test_count=100,
            expected_pass_rate=1.0,
            expected_duration_seconds=60,
        )
        pipe.record_outcome(
            prediction_id=pred.prediction_id,
            actual_test_count=100,
            actual_pass_rate=1.0,
            actual_duration_seconds=60,
        )
    ok, msg = pipe.verify_chain()
    assert ok is True
    assert "20 records" in msg


def test_chain_integrity_tampered(tmp_path: object) -> None:
    path = str(tmp_path) + "/ci-calibration.jsonl"
    pipe = CIPipeline(path=path)
    pred = pipe.record_prediction(
        expected_test_count=100,
        expected_pass_rate=1.0,
        expected_duration_seconds=60,
    )
    pipe.record_outcome(
        prediction_id=pred.prediction_id,
        actual_test_count=100,
        actual_pass_rate=1.0,
        actual_duration_seconds=60,
    )
    # Tamper with a record (compact JSON — no spaces after colons)
    with open(path) as f:
        lines = f.readlines()
    lines[0] = lines[0].replace('"expected_test_count":100', '"expected_test_count":999')
    with open(path, "w") as f:
        f.writelines(lines)

    ok, msg = pipe.verify_chain()
    assert ok is False
    assert "chain break" in msg
