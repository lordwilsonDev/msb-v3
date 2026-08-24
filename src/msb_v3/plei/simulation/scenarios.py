"""Scenarios — what-if engine: compare baseline against modified configurations.

The what-if engine answers questions like:
    "What if we fix the top debt item?"
    "What if provider DeepSeek goes down for 24h?"
    "What if we close 2 of the 6 capability gaps?"

Each scenario modifies one or more simulation variables and re-runs the
Monte Carlo. The output is a side-by-side comparison against baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from msb_v3.plei.simulation.distributions import Fixed
from msb_v3.plei.simulation.monte_carlo import (
    MonteCarloResult,
    SimConfig,
    SimVariable,
    monte_carlo_as_dict,
    run_monte_carlo,
)


@dataclass(slots=True)
class Scenario:
    """One scenario — a named modification to the baseline."""

    name: str
    description: str
    # Which variables to disable (by name prefix match)
    disable_variables: list[str] = field(default_factory=list)
    # Which variables to fix to specific multipliers (by name prefix match)
    fix_variables: dict[str, float] = field(default_factory=dict)
    # Multiplier applied to all failure probabilities (0 = none fire, 1 = unchanged)
    failure_probability_multiplier: float = 1.0


@dataclass(slots=True)
class ScenarioResult:
    """One scenario run against baseline."""

    name: str
    p50_duration: float
    p80_duration: float
    p95_duration: float
    mean_duration: float
    failure_probability: float
    vs_baseline_p50: float  # delta from baseline P50
    vs_baseline_p80: float
    vs_baseline_p95: float
    savings_pct: float  # % improvement over baseline P50
    recommendation: str = ""


@dataclass(slots=True)
class WhatIfReport:
    """Multiple scenarios compared to baseline."""

    baseline: MonteCarloResult
    scenarios: list[ScenarioResult] = field(default_factory=list)
    best_scenario: str = ""
    worst_scenario: str = ""


def _matches(name: str, prefixes: list[str]) -> bool:
    return any(name.startswith(p) for p in prefixes)


def run_what_if(
    config: SimConfig,
    scenarios: list[Scenario],
    *,
    seed: int = 42,
    trial_count: int = 5000,
) -> WhatIfReport:
    """Run baseline and all scenarios, returning a comparison."""
    baseline = run_monte_carlo(config, seed=seed, trial_count=trial_count)
    results: list[ScenarioResult] = []

    for sc in scenarios:
        # Build modified variable list
        modified: list[SimVariable] = []
        for var in config.variables:
            if _matches(var.name, sc.disable_variables):
                # Disable this variable entirely (no impact)
                continue
            rv = var
            if _matches(var.name, list(sc.fix_variables.keys())):
                # Find the fix value for this prefix
                for prefix, fix_val in sc.fix_variables.items():
                    if var.name.startswith(prefix):
                        rv = SimVariable(
                            name=var.name,
                            dist=Fixed(fix_val),
                            category=var.category,
                            is_failure_event=False,
                            description=var.description,
                        )
                        break
            if var.is_failure_event and sc.failure_probability_multiplier != 1.0:
                rv = SimVariable(
                    name=var.name,
                    dist=var.dist,
                    category=var.category,
                    is_failure_event=True,
                    bernoulli_prob=min(0.95, var.bernoulli_prob * sc.failure_probability_multiplier),
                    description=var.description,
                )
            modified.append(rv)

        sc_config = SimConfig(
            variables=modified,
            trial_count=trial_count,
            seed=seed + 1,
            base_duration_days=config.base_duration_days,
            base_cost=config.base_cost,
        )
        sc_result = run_monte_carlo(sc_config, seed=seed + 1, trial_count=trial_count)

        savings = round(
            (baseline.p50_duration - sc_result.p50_duration) / baseline.p50_duration * 100, 1
        ) if baseline.p50_duration > 0 else 0.0

        results.append(ScenarioResult(
            name=sc.name,
            p50_duration=sc_result.p50_duration,
            p80_duration=sc_result.p80_duration,
            p95_duration=sc_result.p95_duration,
            mean_duration=sc_result.mean_duration,
            failure_probability=sc_result.failure_probability,
            vs_baseline_p50=round(sc_result.p50_duration - baseline.p50_duration, 2),
            vs_baseline_p80=round(sc_result.p80_duration - baseline.p80_duration, 2),
            vs_baseline_p95=round(sc_result.p95_duration - baseline.p95_duration, 2),
            savings_pct=savings,
            recommendation=_scenario_recommendation(savings, sc),
        ))

    best = max(results, key=lambda r: r.savings_pct) if results else None
    worst = min(results, key=lambda r: r.savings_pct) if results else None

    return WhatIfReport(
        baseline=baseline,
        scenarios=results,
        best_scenario=best.name if best else "",
        worst_scenario=worst.name if worst else "",
    )


def _scenario_recommendation(savings_pct: float, sc: Scenario) -> str:
    if savings_pct > 10:
        return f"High impact ({savings_pct}% faster) — {sc.description}. Prioritize this."
    elif savings_pct > 3:
        return f"Moderate impact ({savings_pct}% faster) — {sc.description}. Worth doing."
    elif savings_pct > 0:
        return f"Small impact ({savings_pct}% faster) — {sc.description}. Defer if costly."
    else:
        return f"No improvement ({savings_pct}%). {sc.description}. Skip."


def scenarios_from_risk(risk_data: dict[str, Any]) -> list[Scenario]:
    """Generate scenarios from the risk report data.

    Creates a set of default what-if scenarios:
      1. Fix top debt item
      2. Eliminate all failure modes
      3. Halve debt priority
    """
    scenarios: list[Scenario] = []

    # Scenario: Fix the #1 debt item
    top_debt = None
    if risk_data.get("debt_report", {}).get("top_5"):
        top_debt = risk_data["debt_report"]["top_5"][0].get("item", "")
    if top_debt:
        scenarios.append(Scenario(
            name="Fix top debt",
            description=f"Resolve: {top_debt[:80]}",
            disable_variables=[f"debt:{top_debt[:30]}"],
        ))

    # Scenario: No failures
    scenarios.append(Scenario(
        name="Zero failures",
        description="All failure events are suppressed (idealized)",
        failure_probability_multiplier=0.0,
    ))

    # Scenario: Halve all failure likelihoods
    scenarios.append(Scenario(
        name="Halve failure rates",
        description="All failure probabilities cut in half",
        failure_probability_multiplier=0.5,
    ))

    # Scenario: Close capability gaps
    scenarios.append(Scenario(
        name="Close gaps",
        description="All capability gaps resolved",
        disable_variables=["gap:"],
    ))

    # Scenario: Pessimistic — double failure rates
    scenarios.append(Scenario(
        name="Pessimistic",
        description="All failure probabilities doubled",
        failure_probability_multiplier=2.0,
    ))

    return scenarios


def what_if_as_dict(report: WhatIfReport) -> dict[str, Any]:
    return {
        "baseline": monte_carlo_as_dict(report.baseline),
        "scenarios": [
            {
                "name": s.name,
                "p50_duration": s.p50_duration,
                "p80_duration": s.p80_duration,
                "p95_duration": s.p95_duration,
                "failure_probability": s.failure_probability,
                "vs_baseline_p50": s.vs_baseline_p50,
                "vs_baseline_p80": s.vs_baseline_p80,
                "vs_baseline_p95": s.vs_baseline_p95,
                "savings_pct": s.savings_pct,
                "recommendation": s.recommendation,
            }
            for s in report.scenarios
        ],
        "best_scenario": report.best_scenario,
        "worst_scenario": report.worst_scenario,
    }