"""Monte Carlo engine — N-trial simulation over uncertain project variables.

Takes the risk ledger from Phase 3 and constructs a simulation:
    1. Each risk item becomes a variable with a distribution
    2. Impact adjusts task duration/cost distributions
    3. Probability drives Bernoulli failure events
    4. N trials produce a distribution of outcomes
    5. Percentiles and statistics are extracted

Deterministic, seedable, stdlib-only. Every run is reproducible.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Any

from msb_v3.plei.simulation.distributions import (
    PERT,
    Dist,
    Triangular,
    from_pessimistic,
)

# ---------------------------------------------------------------------------
# Simulation inputs
# ---------------------------------------------------------------------------


@dataclass
class SimVariable:
    """One simulation input variable."""

    name: str
    dist: Dist
    category: str = ""  # "duration", "cost", "risk", "debt"
    is_failure_event: bool = False  # Bernoulli: fires with bernoulli_prob, samples dist for delay
    bernoulli_prob: float = 0.0  # probability the failure event fires (0–1)
    description: str = ""


@dataclass(slots=True)
class SimConfig:
    """Simulation configuration."""

    variables: list[SimVariable] = field(default_factory=list)
    trial_count: int = 10000
    seed: int = 42
    base_duration_days: float = 30.0  # assumed project duration in days
    base_cost: float = 0.0  # assumed project cost (0 = unmodeled)


@dataclass(slots=True)
class SimResult:
    """Results from one simulation trial."""

    trial: int
    total_duration: float  # total days (adjusted by risk variables)
    total_cost: float  # total cost (adjusted by risk variables)
    failure_count: int  # how many failure events fired
    debt_impact: float  # cumulative debt penalty
    variables: dict[str, float] = field(default_factory=dict)  # individual variable draws


@dataclass(slots=True)
class MonteCarloResult:
    """Aggregated Monte Carlo results."""

    trial_count: int
    seed: int
    elapsed_s: float
    durations: list[float] = field(default_factory=list)
    costs: list[float] = field(default_factory=list)
    failures: list[int] = field(default_factory=list)
    p50_duration: float = 0.0
    p80_duration: float = 0.0
    p95_duration: float = 0.0
    mean_duration: float = 0.0
    stdev_duration: float = 0.0
    p50_cost: float = 0.0
    p80_cost: float = 0.0
    p95_cost: float = 0.0
    failure_probability: float = 0.0  # trials where at least one failure event fired
    avg_failure_count: float = 0.0
    coefficient_of_variation: float = 0.0  # stdev / mean — uncertainty ratio


# ---------------------------------------------------------------------------
# Variable construction from risk data
# ---------------------------------------------------------------------------


def variables_from_risk_report(
    risk_data: dict[str, Any],
    base_duration_days: float = 30.0,
) -> list[SimVariable]:
    """Convert the Phase 3 risk report into simulation variables.

    Each risk item becomes a variable:
      - Debt items → PERT distributions on duration/cost impact
      - Failure modes → Bernoulli events (probability from likelihood)
      - Bottlenecks → Triangular distributions on delay

    The mapping is deterministic and auditable.
    """
    variables: list[SimVariable] = []

    debt = risk_data.get("debt_report", {})
    for item in debt.get("top_5", []) + list(debt.get("by_class", {}).values())[:5]:
        items = item if isinstance(item, list) else [item]
        for d in items:
            if not isinstance(d, dict):
                continue
            if d.get("priority", 0) < 2.0:
                continue
            # Each debt item adds uncertainty to duration
            impact = d.get("impact", 5) / 10.0  # normalize to 0.0–1.0
            # Duration multiplier: if debt fires, add impact × base_duration
            mode_mult = 1.0 + impact * 0.15  # 0%–15% added
            high_mult = 1.0 + impact * 0.40  # 0%–40% worst case
            variables.append(SimVariable(
                name=f"debt:{d.get('item', 'unknown')[:50]}",
                dist=PERT(low=1.0, mode=mode_mult, high=high_mult),
                category="debt",
                is_failure_event=False,
                description=str(d.get("note", d.get("item", ""))),
            ))

    failures = risk_data.get("failure_report", {})
    for fm in failures.get("modes", []):
        if not isinstance(fm, dict):
            continue
        likelihood = fm.get("likelihood", 0.3)
        severity = fm.get("severity", 5) / 10.0
        if likelihood < 0.03:
            continue
        # Failure delay distribution — how much delay if it fires
        delay_days = base_duration_days * severity * 0.20
        variables.append(SimVariable(
            name=f"failure:{fm.get('kind', 'unknown')}:{fm.get('component', '')[:30]}",
            dist=Triangular(
                low=delay_days * 0.3,
                mode=delay_days,
                high=delay_days * 3.0,
            ),
            category="failure",
            is_failure_event=True,
            bernoulli_prob=likelihood,
            description=fm.get("evidence", ""),
        ))

    return variables


def variables_from_gaps(
    gaps_data: dict[str, Any],
    base_duration_days: float = 30.0,
) -> list[SimVariable]:
    """Convert Phase 2 capability gaps into simulation variables.

    Each MISSING or PARTIAL gap adds uncertainty to the project timeline.
    """
    variables: list[SimVariable] = []
    for gap in gaps_data.get("gaps", []):
        if not isinstance(gap, dict):
            continue
        if gap.get("status") == "COVERED":
            continue
        criticality = gap.get("criticality", 5) / 10.0
        status = gap.get("status", "MISSING")
        delay_mult = 1.0 + criticality * (0.3 if status == "MISSING" else 0.15)
        variables.append(SimVariable(
            name=f"gap:{gap.get('capability', 'unknown')}",
            dist=from_pessimistic(delay_mult, uncertainty=0.25),
            category="gap",
            description=gap.get("recommendation", ""),
        ))
    return variables


# ---------------------------------------------------------------------------
# Simulation runner
# ---------------------------------------------------------------------------


def run_monte_carlo(
    config: SimConfig,
    *,
    seed: int | None = None,
    trial_count: int | None = None,
) -> MonteCarloResult:
    """Run N Monte Carlo trials over the configured variables.

    Each trial:
      1. Samples every variable independently
      2. Applies duration/cost multipliers from non-failure variables
      3. Rolls Bernoulli for failure events, adding delay if they fire
      4. Computes cumulative debt impact
      5. Records the trial

    Returns aggregated statistics: P50, P80, P95, failure probability, etc.
    """
    rng = random.Random(seed if seed is not None else config.seed)
    n = trial_count or config.trial_count
    base = config.base_duration_days
    started = time.perf_counter()

    durations: list[float] = []
    costs: list[float] = []
    failures: list[int] = []

    for trial_i in range(n):
        duration = base
        cost = config.base_cost
        failure_count = 0
        debt_impact = 0.0

        for var in config.variables:
            if var.is_failure_event:
                # Bernoulli: fire with bernoulli_prob, then sample delay
                prob = min(var.bernoulli_prob, 0.95)
                if prob > 0 and rng.random() < prob:
                    delay = abs(var.dist.sample(rng))
                    delay = min(delay, base * 2.0)  # cap at 2× project duration
                    duration += delay
                    cost += delay * 0.1
                    failure_count += 1
            else:
                # Additive impact: each variable adds a fraction of base duration
                multiplier = var.dist.sample(rng)
                added = max(0, (multiplier - 1.0) * base)
                added = min(added, base * 2.0)  # cap per variable
                duration += added
                if var.category == "debt":
                    debt_impact += added

        durations.append(round(duration, 2))
        costs.append(round(cost, 2))
        failures.append(failure_count)

    elapsed = round(time.perf_counter() - started, 3)

    # Sort for percentiles
    sorted_d = sorted(durations)
    sorted_c = sorted(costs)

    mean_d = sum(durations) / n if n > 0 else 0.0
    variance = sum((d - mean_d) ** 2 for d in durations) / n if n > 0 else 0.0
    stdev_d = math.sqrt(variance)
    cv = stdev_d / mean_d if mean_d > 0 else 0.0

    return MonteCarloResult(
        trial_count=n,
        seed=seed or config.seed,
        elapsed_s=elapsed,
        durations=durations,
        costs=costs,
        failures=failures,
        p50_duration=_percentile(sorted_d, 0.50),
        p80_duration=_percentile(sorted_d, 0.80),
        p95_duration=_percentile(sorted_d, 0.95),
        mean_duration=round(mean_d, 2),
        stdev_duration=round(stdev_d, 2),
        p50_cost=_percentile(sorted_c, 0.50),
        p80_cost=_percentile(sorted_c, 0.80),
        p95_cost=_percentile(sorted_c, 0.95),
        failure_probability=round(sum(1 for f in failures if f > 0) / n, 3) if n > 0 else 0.0,
        avg_failure_count=round(sum(failures) / n, 2) if n > 0 else 0.0,
        coefficient_of_variation=round(cv, 3),
    )


def _percentile(sorted_data: list[float], pct: float) -> float:
    """P-th percentile from sorted data (linear interpolation)."""
    if not sorted_data:
        return 0.0
    n = len(sorted_data)
    k = (n - 1) * pct
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    d0 = sorted_data[f] * (c - k)
    d1 = sorted_data[c] * (k - f)
    return round(d0 + d1, 2)


def monte_carlo_as_dict(result: MonteCarloResult) -> dict[str, Any]:
    return {
        "trial_count": result.trial_count,
        "seed": result.seed,
        "elapsed_s": result.elapsed_s,
        "duration": {
            "p50": result.p50_duration,
            "p80": result.p80_duration,
            "p95": result.p95_duration,
            "mean": result.mean_duration,
            "stdev": result.stdev_duration,
            "coefficient_of_variation": result.coefficient_of_variation,
        },
        "cost": {
            "p50": result.p50_cost,
            "p80": result.p80_cost,
            "p95": result.p95_cost,
        },
        "failure_probability": result.failure_probability,
        "avg_failure_count": result.avg_failure_count,
    }