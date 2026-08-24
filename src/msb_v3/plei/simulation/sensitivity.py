"""Sensitivity analysis — tornado diagrams: which variables drive uncertainty?

Runs the Monte Carlo with one variable held fixed at a time, then measures
how much the outcome changes. Variables whose removal changes the result most
are the ones driving uncertainty — these should get the most attention.

This is the complement of Monte Carlo:
    Monte Carlo → "what could happen?"
    Sensitivity → "what's actually driving the uncertainty?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from msb_v3.plei.simulation.distributions import Fixed
from msb_v3.plei.simulation.monte_carlo import (
    SimConfig,
    SimVariable,
    run_monte_carlo,
)


@dataclass(slots=True)
class SensitivityItem:
    """One variable's sensitivity result."""

    variable: str
    category: str
    baseline_p50: float
    without_variable_p50: float
    delta: float  # baseline - without (positive = variable increases duration)
    contribution_pct: float  # share of total variance this variable explains


@dataclass(slots=True)
class SensitivityReport:
    """Tornado analysis — ranked sensitivity items."""

    baseline_p50: float
    items: list[SensitivityItem] = field(default_factory=list)
    total_variance_explained: float = 0.0
    top_drivers: list[str] = field(default_factory=list)


def analyze_sensitivity(
    config: SimConfig,
    *,
    seed: int = 42,
    trial_count: int = 3000,
) -> SensitivityReport:
    """Run sensitivity analysis: remove one variable at a time, measure delta.

    For each variable, runs the simulation with that variable fixed to its
    median value (turned into a Fixed distribution). The delta between
    baseline P50 and the P50 without that variable tells us how much
    uncertainty that variable contributes.
    """
    baseline = run_monte_carlo(config, seed=seed, trial_count=trial_count)
    items: list[SensitivityItem] = []

    total_abs_delta = 0.0

    for var in config.variables:
        # Build config without this variable (fixed to its mode/median)
        modified: list[SimVariable] = []
        for v in config.variables:
            if v.name == var.name:
                # Fix this variable to its mode (or median for Normal)
                fixed_value = v.dist.mode if hasattr(v.dist, 'mode') else getattr(v.dist, 'mean', 1.0)
                modified.append(SimVariable(
                    name=v.name,
                    dist=Fixed(fixed_value),
                    category=v.category,
                    is_failure_event=False,
                    description=v.description,
                ))
            else:
                modified.append(v)

        sc_config = SimConfig(
            variables=modified,
            trial_count=trial_count,
            seed=seed + 1,
            base_duration_days=config.base_duration_days,
        )
        without = run_monte_carlo(sc_config, seed=seed + 1, trial_count=trial_count)

        delta = round(baseline.p50_duration - without.p50_duration, 2)
        total_abs_delta += abs(delta)

        items.append(SensitivityItem(
            variable=var.name,
            category=var.category,
            baseline_p50=baseline.p50_duration,
            without_variable_p50=without.p50_duration,
            delta=delta,
            contribution_pct=0.0,  # computed after
        ))

    # Compute contribution percentages
    if total_abs_delta > 0:
        for item in items:
            item.contribution_pct = round(abs(item.delta) / total_abs_delta * 100, 1)

    # Sort by absolute contribution
    items.sort(key=lambda i: -abs(i.delta))

    total_variance = sum(abs(i.delta) for i in items)
    top_drivers = [i.variable for i in items[:3] if abs(i.delta) > 0.01]

    return SensitivityReport(
        baseline_p50=baseline.p50_duration,
        items=items,
        total_variance_explained=round(total_variance, 2),
        top_drivers=top_drivers,
    )


def sensitivity_as_dict(report: SensitivityReport) -> dict[str, Any]:
    return {
        "baseline_p50": report.baseline_p50,
        "total_variance_explained": report.total_variance_explained,
        "top_drivers": report.top_drivers,
        "items": [
            {
                "variable": i.variable,
                "category": i.category,
                "delta": i.delta,
                "contribution_pct": i.contribution_pct,
                "baseline_p50": i.baseline_p50,
                "without_variable_p50": i.without_variable_p50,
            }
            for i in report.items
        ],
    }