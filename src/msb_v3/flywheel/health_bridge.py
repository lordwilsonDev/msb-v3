"""Flywheel health bridge — reads system health to inform flywheel decisions.

The flywheel engine runs autonomously through 9 stages. The health bridge
gives it visibility into the wider system so it can make informed choices:
- Should it pause new turns? (API error rate too high)
- Which charger backend should it use? (Ollama healthy → sovereign, else stub)
- What's the current system health? (for the health endpoint)

This is a read-only bridge — it never modifies system state. It reads from:
- Prometheus metrics (latency, error rate, active connections)
- System health endpoint (component status)
- Flywheel's own metrics (active turns, stage outcomes)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict

from msb_v3.observability.metrics import (
    FLYWHEEL_ACTIVE_TURNS,
    FLYWHEEL_STAGE_RESULT,
    LATENCY,
    READY,
)

logger = logging.getLogger(__name__)

# Thresholds for health decisions
_ERROR_RATE_THRESHOLD = 0.5  # pause if >50% of recent stages errored
_LATENCY_THRESHOLD_S = 5.0   # warn if p50 latency > 5s


@dataclass
class FlywheelHealth:
    """Structured health status for the flywheel subsystem."""

    active_turns: int = 0
    recent_error_rate: float = 0.0
    recent_pass_rate: float = 0.0
    system_ready: bool = False
    api_latency_p50: float = 0.0
    should_pause: bool = False
    pause_reason: str = ""
    recommended_charger: str = "stub"
    overall_status: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_turns": self.active_turns,
            "recent_error_rate": round(self.recent_error_rate, 4),
            "recent_pass_rate": round(self.recent_pass_rate, 4),
            "system_ready": self.system_ready,
            "api_latency_p50": round(self.api_latency_p50, 4),
            "should_pause": self.should_pause,
            "pause_reason": self.pause_reason,
            "recommended_charger": self.recommended_charger,
            "overall_status": self.overall_status,
        }


def read_flywheel_health() -> FlywheelHealth:
    """Read current flywheel health from Prometheus metrics.

    This is pure read-only — no side effects, no network calls.
    All data comes from metrics that are already being collected.
    """
    health = FlywheelHealth()

    # Active turns
    health.active_turns = int(FLYWHEEL_ACTIVE_TURNS._value.get())

    # System readiness
    health.system_ready = READY._value.get() == 1

    # Stage outcome rates (from counters)
    total_pass = sum(
        FLYWHEEL_STAGE_RESULT.labels(stage=s, result="pass")._value.get()
        for s in ("verify_novelty", "draft_blueprint", "charge", "update_blueprint",
                   "scan_papers", "surface_problems", "build", "combine", "record")
    )
    total_error = sum(
        FLYWHEEL_STAGE_RESULT.labels(stage=s, result="error")._value.get()
        for s in ("verify_novelty", "draft_blueprint", "charge", "update_blueprint",
                   "scan_papers", "surface_problems", "build", "combine", "record")
    )
    total_blocked = sum(
        FLYWHEEL_STAGE_RESULT.labels(stage=s, result="blocked")._value.get()
        for s in ("verify_novelty", "draft_blueprint", "charge", "update_blueprint",
                   "scan_papers", "surface_problems", "build", "combine", "record")
    )
    total = total_pass + total_error + total_blocked
    if total > 0:
        health.recent_error_rate = total_error / total
        health.recent_pass_rate = total_pass / total

    # API latency (from the LATENCY histogram)
    # prometheus_client Histogram doesn't expose p50 directly,
    # but we can read the sum and count to compute average
    try:
        latency_sum = LATENCY.labels(harness="default")._sum.get()
        latency_count = LATENCY.labels(harness="default")._count.get()
        if latency_count > 0:
            health.api_latency_p50 = latency_sum / latency_count
    except Exception:
        pass

    # Pause decision
    if health.recent_error_rate > _ERROR_RATE_THRESHOLD and total >= 5:
        health.should_pause = True
        health.pause_reason = f"error rate {health.recent_error_rate:.1%} > {_ERROR_RATE_THRESHOLD:.0%} threshold"
    elif not health.system_ready and health.active_turns > 0:
        health.should_pause = True
        health.pause_reason = "system not ready but turns are active"

    # Charger recommendation
    if health.system_ready and health.recent_error_rate < 0.3:
        health.recommended_charger = "sovereign"
    else:
        health.recommended_charger = "stub"

    # Overall status
    if health.should_pause:
        health.overall_status = "paused"
    elif health.recent_error_rate > 0.2:
        health.overall_status = "degraded"
    elif health.active_turns > 0:
        health.overall_status = "running"
    else:
        health.overall_status = "idle"

    return health
