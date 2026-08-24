"""Forecast — project trajectories and milestone predictions.

Takes the Monte Carlo result and produces:
    1. Duration forecast: P50/P80/P95 completion times
    2. Milestone probability: chance of finishing by target date
    3. Trajectory projection: likely path through the project
    4. Risk contribution: which categories contribute most to delay

The forecast is honest about its assumptions — every output carries the
simulation parameters and distribution choices it was derived from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from msb_v3.plei.simulation.monte_carlo import MonteCarloResult


@dataclass(slots=True)
class Milestone:
    """A named milestone with a target date."""

    name: str
    target_day: float  # target completion day
    probability: float = 0.0  # chance of hitting this milestone


@dataclass(slots=True)
class Forecast:
    """Project forecast — duration, milestones, trajectory."""

    # Duration
    p50_days: float
    p80_days: float
    p95_days: float
    mean_days: float
    stdev_days: float
    range_days: str  # "P50–P95" formatted

    # Uncertainty
    coefficient_of_variation: float  # >0.3 = high uncertainty
    uncertainty_level: str  # "low", "moderate", "high", "extreme"

    # Milestones
    milestones: list[Milestone] = field(default_factory=list)

    # Trajectory
    trajectory: str = ""  # prose description
    recommendation: str = ""

    # Assumptions
    trial_count: int = 0
    seed: int = 0
    elapsed_s: float = 0.0


def compute_milestones(
    result: MonteCarloResult,
    milestones: list[tuple[str, float]],
) -> list[Milestone]:
    """Compute probability of hitting each milestone.

    For each target day, count how many trials finished on or before it.
    """
    if not result.durations:
        return []
    n = len(result.durations)
    return [
        Milestone(
            name=name,
            target_day=target,
            probability=round(sum(1 for d in result.durations if d <= target) / n, 3),
        )
        for name, target in milestones
    ]


def build_forecast(
    result: MonteCarloResult,
    *,
    project_name: str = "",
    target_days: float = 0.0,
) -> Forecast:
    """Build a full forecast from Monte Carlo results."""

    # Uncertainty level
    cv = result.coefficient_of_variation
    if cv < 0.15:
        uncertainty_level = "low"
    elif cv < 0.30:
        uncertainty_level = "moderate"
    elif cv < 0.50:
        uncertainty_level = "high"
    else:
        uncertainty_level = "extreme"

    range_str = f"{result.p50_duration:.0f}–{result.p95_duration:.0f} days"

    # Milestones
    milestones: list[Milestone] = []
    if target_days > 0:
        milestones = compute_milestones(result, [
            ("Optimistic", result.p50_duration),
            ("Target", target_days),
            ("Pessimistic", result.p95_duration),
        ])

    # Trajectory description
    trajectory = _trajectory_prose(result, project_name)

    # Recommendation
    recommendation = _forecast_recommendation(result)

    return Forecast(
        p50_days=result.p50_duration,
        p80_days=result.p80_duration,
        p95_days=result.p95_duration,
        mean_days=result.mean_duration,
        stdev_days=result.stdev_duration,
        range_days=range_str,
        coefficient_of_variation=cv,
        uncertainty_level=uncertainty_level,
        milestones=milestones,
        trajectory=trajectory,
        recommendation=recommendation,
        trial_count=result.trial_count,
        seed=result.seed,
        elapsed_s=result.elapsed_s,
    )


def _trajectory_prose(result: MonteCarloResult, name: str) -> str:
    name = name or "project"
    cv = result.coefficient_of_variation
    spread = result.p95_duration - result.p50_duration

    if cv < 0.15:
        return (
            f"{name} has a tightly-bounded trajectory — CV={cv:.3f} indicates low outcome variance. "
            f"{result.failure_probability:.0%} of trials hit one or more failure events "
            f"(avg {result.avg_failure_count}/trial), but the impact is well-contained: "
            f"P50={result.p50_duration:.0f}d, P95={result.p95_duration:.0f}d (spread: {spread:.0f}d)."
        )
    elif cv < 0.30:
        return (
            f"{name} has moderate tail risk — {result.failure_probability:.0%} of trials "
            f"hit at least one failure event (avg {result.avg_failure_count}/trial). "
            f"P50={result.p50_duration:.0f}d to P95={result.p95_duration:.0f}d (spread: {spread:.0f}d)."
        )
    else:
        return (
            f"{name} has significant uncertainty — {result.failure_probability:.0%} "
            f"failure probability with {result.avg_failure_count} failures/trial on average. "
            f"P50={result.p50_duration:.0f}d, P95={result.p95_duration:.0f}d. "
            f"The wide spread ({spread:.0f}d) suggests the plan is sensitive to failure events."
        )


def _forecast_recommendation(result: MonteCarloResult) -> str:
    cv = result.coefficient_of_variation
    fp = result.failure_probability

    if cv < 0.15 and fp < 0.10:
        return "Low uncertainty — the project plan is well-bounded. Proceed with normal monitoring."
    elif cv < 0.30 or fp < 0.25:
        return "Moderate uncertainty — consider adding buffer for the top uncertainty driver. Review sensitivity analysis."
    elif cv < 0.50:
        return "High uncertainty — the plan is sensitive to several variables. Address the top risk before committing to a date."
    else:
        return "Extreme uncertainty — the current plan cannot produce a reliable estimate. Reduce unknowns before forecasting."


def forecast_as_dict(forecast: Forecast) -> dict[str, Any]:
    return {
        "duration": {
            "p50_days": forecast.p50_days,
            "p80_days": forecast.p80_days,
            "p95_days": forecast.p95_days,
            "mean_days": forecast.mean_days,
            "stdev_days": forecast.stdev_days,
            "range": forecast.range_days,
        },
        "uncertainty": {
            "coefficient_of_variation": forecast.coefficient_of_variation,
            "level": forecast.uncertainty_level,
        },
        "milestones": [
            {
                "name": m.name,
                "target_day": m.target_day,
                "probability": m.probability,
            }
            for m in forecast.milestones
        ],
        "trajectory": forecast.trajectory,
        "recommendation": forecast.recommendation,
        "parameters": {
            "trial_count": forecast.trial_count,
            "seed": forecast.seed,
            "elapsed_s": forecast.elapsed_s,
        },
    }