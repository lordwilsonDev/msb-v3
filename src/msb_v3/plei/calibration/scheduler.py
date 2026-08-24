"""Calibration Scheduler — decide when to trigger re-calibration.

Three trigger conditions (any one fires):
    1. COUNT: every N new prediction/outcome pairs (default: 5)
    2. TIME: after M days since last calibration (default: 7)
    3. DRIFT: if MAPE or ECE has degraded by >threshold (default: 0.10)

Schedulers are immutable snapshots of calibration state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from msb_v3.plei.calibration.error import ErrorMetrics
from msb_v3.plei.calibration.store import CalibrationStore


@dataclass(slots=True)
class CalibrationSchedule:
    """Current calibration scheduling state."""

    total_predictions: int
    total_outcomes: int
    total_pairs: int

    # Count trigger
    pairs_since_last_calibration: int = 0
    count_threshold: int = 5
    count_triggered: bool = False

    # Time trigger
    last_calibration_at: str = ""  # ISO-8601
    days_since_last: int = 0
    time_threshold_days: int = 7
    time_triggered: bool = False

    # Drift trigger
    current_mape: float = 0.0
    previous_mape: float = 0.0
    mape_delta: float = 0.0
    drift_threshold: float = 0.10
    drift_triggered: bool = False

    # Overall
    should_calibrate: bool = False
    reason: str = ""


def compute_schedule(
    store: CalibrationStore,
    *,
    count_threshold: int = 5,
    time_threshold_days: int = 7,
    drift_threshold: float = 0.10,
    previous_error: ErrorMetrics | None = None,
) -> CalibrationSchedule:
    """Determine whether calibration should run now.

    Reads the calibration store to count pairs, then evaluates all three
    trigger conditions. Returns a CalibrationSchedule with the decision.
    """
    total_pred = store.prediction_count()
    total_out = store.outcome_count()
    pairs = store.pairs()
    n_pairs = len(pairs)

    sched = CalibrationSchedule(
        total_predictions=total_pred,
        total_outcomes=total_out,
        total_pairs=n_pairs,
        pairs_since_last_calibration=n_pairs,
        count_threshold=count_threshold,
        time_threshold_days=time_threshold_days,
        drift_threshold=drift_threshold,
    )

    # ── Count trigger ──
    if n_pairs >= count_threshold and n_pairs > 0:
        sched.count_triggered = True

    # ── Time trigger ──
    if pairs:
        # Use the earliest prediction time as the last calibration timestamp
        try:
            pred_times = [p.prediction.forecast_at for p in pairs]
            if pred_times:
                earliest = min(pred_times)
                sched.last_calibration_at = earliest
                try:
                    last_dt = datetime.fromisoformat(earliest.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    sched.days_since_last = (now - last_dt).days
                    if sched.days_since_last >= time_threshold_days:
                        sched.time_triggered = True
                except (ValueError, TypeError):
                    pass
        except Exception:
            pass

    # ── Drift trigger ──
    if previous_error is not None and pairs:
        # Compute current MAPE from pairs
        from msb_v3.plei.calibration.error import compute_error_metrics

        current = compute_error_metrics(pairs)
        sched.current_mape = current.mape
        sched.previous_mape = previous_error.mape
        if previous_error.mape > 0:
            sched.mape_delta = abs(current.mape - previous_error.mape)
            if sched.mape_delta > drift_threshold and n_pairs > 0:
                sched.drift_triggered = True

    # ── Overall decision ──
    triggers: list[str] = []
    if sched.count_triggered:
        triggers.append(f"count ({n_pairs} >= {count_threshold})")
    if sched.time_triggered:
        triggers.append(f"time ({sched.days_since_last}d >= {time_threshold_days}d)")
    if sched.drift_triggered:
        triggers.append(f"drift (ΔMAPE={sched.mape_delta:.2f} > {drift_threshold:.2f})")

    if triggers:
        sched.should_calibrate = True
        sched.reason = " | ".join(triggers)
    elif n_pairs == 0:
        sched.reason = "no calibration pairs yet"
    else:
        sched.reason = "no triggers met"

    return sched


# ── Serialization ───────────────────────────────────────────────────────────


def schedule_as_dict(sched: CalibrationSchedule) -> dict[str, Any]:
    return {
        "total_predictions": sched.total_predictions,
        "total_outcomes": sched.total_outcomes,
        "total_pairs": sched.total_pairs,
        "count_trigger": {
            "pairs_since_last": sched.pairs_since_last_calibration,
            "threshold": sched.count_threshold,
            "triggered": sched.count_triggered,
        },
        "time_trigger": {
            "last_calibration_at": sched.last_calibration_at,
            "days_since_last": sched.days_since_last,
            "threshold_days": sched.time_threshold_days,
            "triggered": sched.time_triggered,
        },
        "drift_trigger": {
            "current_mape": sched.current_mape,
            "previous_mape": sched.previous_mape,
            "mape_delta": sched.mape_delta,
            "threshold": sched.drift_threshold,
            "triggered": sched.drift_triggered,
        },
        "should_calibrate": sched.should_calibrate,
        "reason": sched.reason,
    }