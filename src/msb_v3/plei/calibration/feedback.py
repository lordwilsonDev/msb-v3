"""Calibration Feedback — adjust distribution parameters from data.

When calibration reveals systematic error (bias, over/under-confidence),
the feedback engine adjusts the simulation parameters so future
predictions are more accurate.

Three adjustment strategies:
    1. BIAS CORRECTION: if P50 consistently over/under-predicts,
       shift the base_duration_days by the mean bias.
    2. VARIANCE SCALING: if stdev is too narrow (overconfident) or too
       wide (underconfident), scale all variable distributions.
    3. PERT LAMBDA TUNING: adjust PERT lambda parameter to shift
       distribution toward optimistic/pessimistic based on bias.

The adjustments are always additive — they stack on top of whatever
parameters the risk model produces, never replacing them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from msb_v3.plei.calibration.error import ErrorMetrics


@dataclass(slots=True)
class CalibrationAdjustment:
    """Recommended parameter adjustments from calibration feedback."""

    # Bias correction
    bias_days: float = 0.0  # add/subtract from base_duration_days
    bias_applied: bool = False

    # Variance scaling
    stdev_scale_factor: float = 1.0  # 1.0 = no change, >1 = widen, <1 = narrow
    stdev_scaling_applied: bool = False

    # PERT lambda
    pert_lambda_shift: float = 0.0  # add to lambda param
    pert_lambda_applied: bool = False

    # Summary
    description: str = "no adjustments needed"
    confidence_gain: float = 0.0  # estimated ECE improvement


@dataclass(slots=True)
class CalibratedParams:
    """Calibrated parameters ready to use in simulation."""

    base_duration_days: float
    uncertainty_multiplier: float = 1.0  # scale factor for variable distributions
    adjustments: CalibrationAdjustment = field(default_factory=CalibrationAdjustment)

    # Metadata
    calibration_pair_count: int = 0
    calibrated_at: str = ""


# ── Compute ────────────────────────────────────────────────────────────────


def compute_adjustments(
    metrics: ErrorMetrics,
    *,
    aggressive: bool = False,
) -> CalibrationAdjustment:
    """Compute parameter adjustments from calibration error metrics.

    Args:
        metrics: ErrorMetrics from compute_error_metrics()
        aggressive: If True, apply larger corrections (2× default)
    """
    adj = CalibrationAdjustment()

    if metrics.pair_count < 3:
        adj.description = "insufficient data for adjustments (need ≥3 pairs)"
        return adj

    # ── Bias correction ──
    if abs(metrics.bias_days) > 0.5:
        # Shift base_duration opposite to bias (corrective)
        correction = -metrics.bias_days
        if not aggressive:
            correction *= 0.5  # partial correction per cycle
        adj.bias_days = round(correction, 2)
        adj.bias_applied = True

    # ── Variance scaling ──
    if metrics.is_overconfident:
        # Predictions too narrow → widen
        scale = 1.0 + (metrics.calibration_error * 2.0)
        if aggressive:
            scale = 1.0 + (metrics.calibration_error * 4.0)
        adj.stdev_scale_factor = round(min(scale, 3.0), 2)
        adj.stdev_scaling_applied = True
    elif metrics.is_underconfident:
        # Predictions too wide → narrow
        scale = 1.0 - (metrics.calibration_error * 1.5)
        if aggressive:
            scale = 1.0 - (metrics.calibration_error * 3.0)
        adj.stdev_scale_factor = round(max(scale, 0.5), 2)
        adj.stdev_scaling_applied = True

    # ── PERT lambda tuning ──
    # Positive bias (actual > predicted) → shift toward pessimistic (lambda +)
    # Negative bias (actual < predicted) → shift toward optimistic (lambda -)
    if abs(metrics.bias_days) > 2.0 and metrics.pair_count >= 5:
        direction = 1.0 if metrics.bias_days > 0 else -1.0
        shift = direction * min(abs(metrics.bias_days) / 10.0, 2.0)
        if not aggressive:
            shift *= 0.5
        adj.pert_lambda_shift = round(shift, 2)
        adj.pert_lambda_applied = True

    # ── Estimated ECE improvement ──
    if adj.bias_applied or adj.stdev_scaling_applied:
        adj.confidence_gain = round(metrics.calibration_error * 0.4, 3)
        if aggressive:
            adj.confidence_gain = round(metrics.calibration_error * 0.7, 3)

    # ── Description ──
    parts: list[str] = []
    if adj.bias_applied:
        parts.append(f"bias {adj.bias_days:+.1f}d")
    if adj.stdev_scaling_applied:
        parts.append(f"stdev ×{adj.stdev_scale_factor:.2f}")
    if adj.pert_lambda_applied:
        parts.append(f"PERT λ{adj.pert_lambda_shift:+.1f}")
    adj.description = "adjust " + ", ".join(parts) if parts else "no adjustments needed"

    return adj


def apply_adjustments(
    base_duration_days: float,
    adjustments: CalibrationAdjustment,
    pair_count: int = 0,
) -> CalibratedParams:
    """Apply computed adjustments to base simulation parameters.

    Returns CalibratedParams ready to feed into SimConfig.
    """
    calibrated_base = base_duration_days + adjustments.bias_days

    return CalibratedParams(
        base_duration_days=round(calibrated_base, 2),
        uncertainty_multiplier=round(adjustments.stdev_scale_factor, 2),
        adjustments=adjustments,
        calibration_pair_count=pair_count,
        calibrated_at="",
    )


# ── Serialization ───────────────────────────────────────────────────────────


def adjustment_as_dict(adj: CalibrationAdjustment) -> dict[str, Any]:
    return {
        "bias_days": adj.bias_days,
        "bias_applied": adj.bias_applied,
        "stdev_scale_factor": adj.stdev_scale_factor,
        "stdev_scaling_applied": adj.stdev_scaling_applied,
        "pert_lambda_shift": adj.pert_lambda_shift,
        "pert_lambda_applied": adj.pert_lambda_applied,
        "description": adj.description,
        "confidence_gain": adj.confidence_gain,
    }


def calibrated_params_as_dict(params: CalibratedParams) -> dict[str, Any]:
    return {
        "base_duration_days": params.base_duration_days,
        "uncertainty_multiplier": params.uncertainty_multiplier,
        "adjustments": adjustment_as_dict(params.adjustments),
        "calibration_pair_count": params.calibration_pair_count,
        "calibrated_at": params.calibrated_at,
    }