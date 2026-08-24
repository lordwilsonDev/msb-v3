"""Calibration Error — quantitative accuracy metrics.

Given a set of CalibrationPairs, compute:
    1. MAPE — Mean Absolute Percentage Error (duration forecasts)
    2. Brier Score — mean squared error of probability forecasts (0–1, lower=better)
    3. Calibration Error — by confidence bucket (over/under-confidence)
    4. Sharpness — standard deviation of predictions (informativeness)
    5. Bias — systematic over/under-prediction direction

All metrics are deterministic and stdlib-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from msb_v3.plei.calibration.store import CalibrationPair


@dataclass(slots=True)
class ErrorMetrics:
    """Aggregate error metrics over N calibration pairs."""

    pair_count: int

    # Duration accuracy
    mape: float = 0.0  # Mean Absolute Percentage Error (0 = perfect)
    mae_days: float = 0.0  # Mean Absolute Error in days
    rmse_days: float = 0.0  # Root Mean Square Error in days
    bias_days: float = 0.0  # positive = under-predicted (actual > predicted)

    # Probability accuracy
    brier_score: float = 0.0  # 0 = perfect, 1 = worst
    brier_failure: float = 0.0  # Brier for failure probability only
    brier_milestone: float = 0.0  # Brier for milestone probabilities

    # Calibration quality
    calibration_error: float = 0.0  # ECE (Expected Calibration Error), 0 = perfect
    is_overconfident: bool = False
    is_underconfident: bool = False
    calibration_status: str = "insufficient_data"  # uncalibrated / calibrating / calibrated / degrading

    # Sharpness
    prediction_stdev: float = 0.0  # stddev of pred_mean across pairs
    outcome_stdev: float = 0.0  # stddev of actual_duration across pairs

    # Summary
    summary: str = ""
    recommendation: str = ""


# ── Compute ────────────────────────────────────────────────────────────────


def compute_error_metrics(pairs: list[CalibrationPair]) -> ErrorMetrics:
    """Compute the full error metrics for a set of calibration pairs.

    Requires at least 3 pairs for meaningful statistics.
    """
    n = len(pairs)
    if n == 0:
        return ErrorMetrics(pair_count=0, summary="No calibration data available.")

    m = ErrorMetrics(pair_count=n)

    # ── Duration errors ──
    dur_errors = [p.duration_error_days for p in pairs]
    m.mape = round(_safe_mean([p.duration_mape for p in pairs]), 4)
    m.mae_days = round(_safe_mean([abs(d) for d in dur_errors]), 2)
    m.rmse_days = round(_safe_rmse(dur_errors), 2)
    m.bias_days = round(_safe_mean(dur_errors), 2)

    # ── Brier scores ──
    m.brier_score = round(_safe_mean([p.failure_brier for p in pairs]), 4)
    m.brier_failure = m.brier_score
    m.brier_milestone = round(_safe_mean([p.milestone_brier for p in pairs]), 4)

    # ── Calibration error (ECE with 5 buckets) ──
    m.calibration_error, m.is_overconfident, m.is_underconfident = _compute_ece_5(pairs)

    # ── Sharpness ──
    pred_means = [p.prediction.predicted_p50_days for p in pairs]
    actuals = [p.outcome.actual_duration_days for p in pairs
               if p.outcome.actual_duration_days > 0]
    m.prediction_stdev = round(_safe_stdev(pred_means), 2)
    m.outcome_stdev = round(_safe_stdev(actuals), 2)

    # ── Status ──
    m = _classify_calibration(m, n)

    # ── Summary ──
    m.summary, m.recommendation = _summarize(m)

    return m


def _compute_ece_5(pairs: list[CalibrationPair]) -> tuple[float, bool, bool]:
    """Compute Expected Calibration Error using 5 confidence buckets.

    Buckets: [0, 0.2), [0.2, 0.4), [0.4, 0.6), [0.6, 0.8), [0.8, 1.0]

    ECE = Σ (B_i / N) * |accuracy_i - confidence_i|
    where confidence_i = mean(predicted_failure_probability) in bucket
          accuracy_i = mean(actual_failure_rate) in bucket

    Returns (ece, is_overconfident, is_underconfident).
    """
    bucket_boundaries = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    bucket_accuracies: list[float] = []
    bucket_confidences: list[float] = []
    bucket_counts: list[int] = []

    for i in range(5):
        lo = bucket_boundaries[i]
        hi = bucket_boundaries[i + 1]
        bucket_pairs = [p for p in pairs if lo <= p.prediction.predicted_failure_probability < hi]
        if (i == 4):  # top bucket includes 1.0
            bucket_pairs = [p for p in pairs if lo <= p.prediction.predicted_failure_probability <= hi]

        bucket_counts.append(len(bucket_pairs))
        if bucket_pairs:
            conf = _safe_mean([p.prediction.predicted_failure_probability for p in bucket_pairs])
            acc = _safe_mean([1.0 if p.outcome.failures_encountered > 0 else 0.0
                              for p in bucket_pairs])
            bucket_confidences.append(conf)
            bucket_accuracies.append(acc)
        else:
            bucket_confidences.append(0.0)
            bucket_accuracies.append(0.0)

    n = len(pairs)
    ece = 0.0
    for count, acc, conf in zip(bucket_counts, bucket_accuracies, bucket_confidences):
        if count > 0:
            ece += (count / n) * abs(acc - conf)

    # Over/under detection: if accuracy < confidence in higher buckets → overconfident
    # If accuracy > confidence → underconfident
    over_buckets = 0
    under_buckets = 0
    for acc, conf, count in zip(bucket_accuracies, bucket_confidences, bucket_counts):
        if count >= 2:
            if acc < conf - 0.05:
                over_buckets += 1
            elif acc > conf + 0.05:
                under_buckets += 1

    return round(ece, 4), over_buckets > 0, under_buckets > 0


def _classify_calibration(m: ErrorMetrics, n: int) -> ErrorMetrics:
    """Classify calibration quality."""
    if n < 3:
        m.calibration_status = "insufficient_data"
    elif m.mape < 0.15 and m.calibration_error < 0.10:
        m.calibration_status = "calibrated"
    elif m.mape < 0.30 and m.calibration_error < 0.20:
        m.calibration_status = "calibrating"
    elif m.mape > 0.50 or m.calibration_error > 0.30:
        m.calibration_status = "degrading"
    else:
        m.calibration_status = "uncalibrated"
    return m


def _summarize(m: ErrorMetrics) -> tuple[str, str]:
    """Produce human-readable summary and recommendation."""
    if m.pair_count == 0:
        return "No calibration data.", "Run at least 3 prediction/outcome cycles."

    parts = [f"{m.pair_count} pairs: MAPE={m.mape:.1%}, Brier={m.brier_score:.3f}, ECE={m.calibration_error:.3f}"]
    if m.is_overconfident:
        parts.append("OVERCONFIDENT — predicted probabilities too high relative to outcomes")
    if m.is_underconfident:
        parts.append("UNDERCONFIDENT — predicted probabilities too low relative to outcomes")

    rec = ""
    if m.is_overconfident:
        rec = "Widen confidence intervals (reduce PERT lambda, increase stdev) and add buffer to P50 estimates."
    elif m.is_underconfident:
        rec = "Narrow confidence intervals (tighten distributions, reduce noise variables)."
    elif m.calibration_status == "calibrated":
        rec = "Calibration is healthy. Continue recording prediction/outcome pairs."
    else:
        rec = "Collect more prediction/outcome pairs to improve calibration."

    return " | ".join(parts), rec


# ── Math helpers ───────────────────────────────────────────────────────────


def _safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _safe_stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _safe_mean(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return (variance ** 0.5) if variance >= 0 else 0.0


def _safe_rmse(values: list[float]) -> float:
    if not values:
        return 0.0
    return (sum(v ** 2 for v in values) / len(values)) ** 0.5


# ── Serialization ──────────────────────────────────────────────────────────


def error_metrics_as_dict(m: ErrorMetrics) -> dict[str, Any]:
    return {
        "pair_count": m.pair_count,
        "mape": m.mape,
        "mae_days": m.mae_days,
        "rmse_days": m.rmse_days,
        "bias_days": m.bias_days,
        "brier_score": m.brier_score,
        "brier_failure": m.brier_failure,
        "brier_milestone": m.brier_milestone,
        "calibration_error": m.calibration_error,
        "is_overconfident": m.is_overconfident,
        "is_underconfident": m.is_underconfident,
        "calibration_status": m.calibration_status,
        "prediction_stdev": m.prediction_stdev,
        "outcome_stdev": m.outcome_stdev,
        "summary": m.summary,
        "recommendation": m.recommendation,
    }