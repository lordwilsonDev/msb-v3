"""Reliability Diagram — bucket-based calibration visualization and drift detection.

Produces a 5-bucket reliability diagram from calibration pairs:
    Bucket 1: [0.0, 0.2)  — "Very low" confidence
    Bucket 2: [0.2, 0.4)  — "Low"
    Bucket 3: [0.4, 0.6)  — "Medium"
    Bucket 4: [0.6, 0.8)  — "High"
    Bucket 5: [0.8, 1.0]  — "Very high"

Each bucket has:
    - confidence (mean predicted probability in that bucket)
    - accuracy (mean observed frequency)
    - count (how many predictions fell in that bucket)

A perfectly calibrated forecaster has confidence ≈ accuracy in every
bucket — the reliability curve follows the diagonal.

Also detects calibration drift: if recent pairs (most recent N/2) have
significantly different error than historical, calibration is drifting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from msb_v3.plei.calibration.store import CalibrationPair


@dataclass(slots=True)
class ReliabilityBucket:
    """One bucket in the reliability diagram."""

    label: str  # "Very low", "Low", "Medium", "High", "Very high"
    range_lo: float
    range_hi: float
    count: int = 0
    confidence: float = 0.0  # mean predicted probability
    accuracy: float = 0.0  # mean observed outcome rate
    gap: float = 0.0  # accuracy - confidence (positive = underconfident)


@dataclass(slots=True)
class ReliabilityDiagram:
    """Full reliability diagram with drift analysis."""

    buckets: list[ReliabilityBucket] = field(default_factory=list)
    total_pairs: int = 0
    ece: float = 0.0  # Expected Calibration Error

    # Drift
    drift_detected: bool = False
    drift_magnitude: float = 0.0
    drift_description: str = ""

    # Shape analysis
    is_s_shaped: bool = False  # overconfident in middle, underconfident at edges
    is_inverted_s: bool = False  # underconfident in middle
    is_monotonic: bool = False  # accuracy increases with confidence


# ── Build ───────────────────────────────────────────────────────────────────


def build_reliability_diagram(pairs: list[CalibrationPair]) -> ReliabilityDiagram:
    """Build the reliability diagram from calibration pairs."""
    boundaries = [
        ("Very low", 0.0, 0.2),
        ("Low", 0.2, 0.4),
        ("Medium", 0.4, 0.6),
        ("High", 0.6, 0.8),
        ("Very high", 0.8, 1.0),
    ]

    buckets: list[ReliabilityBucket] = []
    for label, lo, hi in boundaries:
        bp = [p for p in pairs if lo <= p.prediction.predicted_failure_probability <= hi
              if not (hi == 0.2 and p.prediction.predicted_failure_probability == 0.2)]  # handle 0.2 edge

        if label == "Very low":
            bp = [p for p in pairs if p.prediction.predicted_failure_probability < 0.2]
        elif label == "Very high":
            bp = [p for p in pairs if 0.8 <= p.prediction.predicted_failure_probability <= 1.0]
        else:
            bp = [p for p in pairs if lo <= p.prediction.predicted_failure_probability < hi]

        bucket = ReliabilityBucket(label=label, range_lo=lo, range_hi=hi, count=len(bp))
        if bp:
            bucket.confidence = round(
                sum(p.prediction.predicted_failure_probability for p in bp) / len(bp), 3,
            )
            bucket.accuracy = round(
                sum(1.0 if p.outcome.failures_encountered > 0 else 0.0 for p in bp) / len(bp), 3,
            )
            bucket.gap = round(bucket.accuracy - bucket.confidence, 3)

        buckets.append(bucket)

    # ECE
    n = len(pairs)
    ece = 0.0
    for b in buckets:
        if b.count > 0:
            ece += (b.count / n) * abs(b.accuracy - b.confidence) if n > 0 else 0.0

    # Shape analysis
    accs = [b.accuracy for b in buckets if b.count >= 2]
    is_monotonic = all(
        accs[i] <= accs[i + 1] for i in range(len(accs) - 1)
    ) if len(accs) >= 2 else False

    # S-shape: overconfident at extremes, underconfident in middle
    is_s = False
    is_inv = False
    if len(buckets) == 5:
        low = buckets[0].gap
        mid = buckets[2].gap
        high = buckets[4].gap
        if buckets[0].count >= 2 and buckets[2].count >= 2 and buckets[4].count >= 2:
            is_s = (low < 0 and mid > 0 and high < 0)  # over-under-over
            is_inv = (low > 0 and mid < 0 and high > 0)  # under-over-under

    # Drift: compare first half vs second half
    drift_detected = False
    drift_mag = 0.0
    drift_desc = ""
    if n >= 6:
        mid = n // 2
        early = pairs[:mid]
        late = pairs[mid:]
        early_mape = _mean_mape(early)
        late_mape = _mean_mape(late)
        if early_mape > 0 and late_mape > 0:
            drift_mag = abs(late_mape - early_mape)
            if drift_mag > 0.15:
                drift_detected = True
                if late_mape > early_mape:
                    drift_desc = "Calibration is worsening — recent predictions are less accurate."
                else:
                    drift_desc = "Calibration is improving — recent predictions are more accurate."

    return ReliabilityDiagram(
        buckets=buckets,
        total_pairs=n,
        ece=round(ece, 4),
        drift_detected=drift_detected,
        drift_magnitude=drift_mag,
        drift_description=drift_desc,
        is_s_shaped=is_s,
        is_inverted_s=is_inv,
        is_monotonic=is_monotonic,
    )


def _mean_mape(pairs: list[CalibrationPair]) -> float:
    if not pairs:
        return 0.0
    mapes = [p.duration_mape for p in pairs]
    return sum(mapes) / len(mapes)


# ── Serialization ───────────────────────────────────────────────────────────


def reliability_as_dict(diagram: ReliabilityDiagram) -> dict[str, Any]:
    return {
        "total_pairs": diagram.total_pairs,
        "ece": diagram.ece,
        "buckets": [
            {
                "label": b.label,
                "range": f"{b.range_lo:.1f}–{b.range_hi:.1f}",
                "count": b.count,
                "confidence": b.confidence,
                "accuracy": b.accuracy,
                "gap": b.gap,
                "interpretation": (
                    "perfect" if abs(b.gap) < 0.03 and b.count > 0
                    else "overconfident" if b.gap < -0.03
                    else "underconfident" if b.gap > 0.03
                    else "insufficient data"
                ),
            }
            for b in diagram.buckets
        ],
        "drift_detected": diagram.drift_detected,
        "drift_magnitude": diagram.drift_magnitude,
        "drift_description": diagram.drift_description,
        "shape": {
            "is_s_shaped": diagram.is_s_shaped,
            "is_inverted_s": diagram.is_inverted_s,
            "is_monotonic": diagram.is_monotonic,
        },
    }