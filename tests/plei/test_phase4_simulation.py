"""PLEI Phase 4 tests — distributions, Monte Carlo, scenarios, sensitivity, forecast.

Tests the simulation engine with deterministic seed for reproducibility.
"""

from __future__ import annotations

import pytest

from msb_v3.plei.simulation.distributions import (
    PERT,
    Fixed,
    Normal,
    Triangular,
    from_expert_triple,
    from_pessimistic,
)
from msb_v3.plei.simulation.forecast import build_forecast, forecast_as_dict
from msb_v3.plei.simulation.monte_carlo import (
    SimConfig,
    SimVariable,
    monte_carlo_as_dict,
    run_monte_carlo,
)
from msb_v3.plei.simulation.scenarios import (
    Scenario,
    run_what_if,
    scenarios_from_risk,
    what_if_as_dict,
)
from msb_v3.plei.simulation.sensitivity import (
    analyze_sensitivity,
    sensitivity_as_dict,
)

# --- Distributions ---

def test_fixed_always_returns_value():
    d = Fixed(42.0)
    for _ in range(100):
        assert d.sample() == 42.0


def test_triangular_in_range():
    d = Triangular(1, 5, 10)
    samples = [d.sample() for _ in range(500)]
    assert all(1.0 <= s <= 10.0 for s in samples), f"Out of range sample: {min(samples)}–{max(samples)}"


def test_pert_in_range():
    d = PERT(1, 5, 10)
    samples = [d.sample() for _ in range(500)]
    assert all(1.0 <= s <= 10.0 for s in samples), f"Out of range: {min(samples)}–{max(samples)}"


def test_normal_around_mean():
    d = Normal(10.0, 2.0)
    samples = [d.sample() for _ in range(5000)]
    avg = sum(samples) / len(samples)
    assert 9.5 <= avg <= 10.5, f"Mean drifted: {avg}"


def test_triangular_mode_near_peak():
    d = Triangular(0, 5, 10)
    samples = [d.sample() for _ in range(5000)]
    avg = sum(samples) / len(samples)
    theoretical = (0 + 5 + 10) / 3  # ~5.0
    assert 4.5 <= avg <= 5.5, f"Triangular mean off: {avg}, expected ~{theoretical}"


def test_validate_rejects_bad_triangular():
    with pytest.raises(ValueError):
        Triangular(10, 5, 1)


def test_from_expert_triple():
    d = from_expert_triple(2, 4, 10)
    assert isinstance(d, Triangular)
    assert d.low == 2
    assert d.mode == 4
    assert d.high == 10


# --- Monte Carlo ---

def test_monte_carlo_with_no_variables():
    config = SimConfig(variables=[], trial_count=500, seed=42, base_duration_days=30.0)
    result = run_monte_carlo(config, seed=42, trial_count=500)
    assert result.trial_count == 500
    assert result.p50_duration == 30.0
    assert result.p80_duration == 30.0
    assert result.p95_duration == 30.0
    assert result.failure_probability == 0.0


def test_monte_carlo_with_fixed_multiplier():
    config = SimConfig(
        variables=[SimVariable(name="test", dist=Fixed(1.5), category="debt")],
        trial_count=500,
        seed=42,
        base_duration_days=30.0,
    )
    result = run_monte_carlo(config, seed=42, trial_count=500)
    # multiplier 1.5 → added = (1.5 - 1.0) * 30 = 15 → duration = 45
    assert result.p50_duration == pytest.approx(45.0, rel=0.05)


def test_monte_carlo_with_failure_events():
    config = SimConfig(
        variables=[
            SimVariable(
                name="test_failure",
                dist=Fixed(10.0),  # delay if it fires
                category="failure",
                is_failure_event=True,
                bernoulli_prob=0.5,
            ),
        ],
        trial_count=2000,
        seed=42,
        base_duration_days=30.0,
    )
    result = run_monte_carlo(config, seed=42, trial_count=2000)
    assert 0.40 <= result.failure_probability <= 0.60, f"Failure prob: {result.failure_probability}"
    assert result.p50_duration >= 30.0  # at least baseline


def test_monte_carlo_is_deterministic():
    config = SimConfig(
        variables=[SimVariable(name="t", dist=Triangular(1, 2, 3), category="debt")],
        trial_count=500,
        seed=42,
        base_duration_days=30.0,
    )
    r1 = run_monte_carlo(config, seed=42, trial_count=500)
    r2 = run_monte_carlo(config, seed=42, trial_count=500)
    assert r1.p50_duration == r2.p50_duration
    assert r1.p95_duration == r2.p95_duration


def test_monte_carlo_as_dict():
    config = SimConfig(variables=[], trial_count=100, seed=42, base_duration_days=30.0)
    result = run_monte_carlo(config, seed=42, trial_count=100)
    d = monte_carlo_as_dict(result)
    import json
    json.dumps(d)
    assert "duration" in d


# --- Percentiles ---

def test_percentile_p50_is_median():
    data = [1.0, 2.0, 3.0, 4.0, 5.0]
    from msb_v3.plei.simulation.monte_carlo import _percentile
    assert _percentile(data, 0.50) == 3.0
    assert _percentile(data, 0.0) == 1.0
    assert _percentile(data, 1.0) == 5.0


# --- Forecast ---

def test_forecast_builds_all_fields():
    config = SimConfig(variables=[], trial_count=100, seed=42, base_duration_days=30.0)
    result = run_monte_carlo(config, seed=42, trial_count=100)
    forecast = build_forecast(result, project_name="test")
    assert forecast.p50_days == 30.0
    assert forecast.uncertainty_level == "low"
    assert forecast.trajectory != ""


def test_forecast_as_dict():
    config = SimConfig(variables=[], trial_count=100, seed=42, base_duration_days=30.0)
    result = run_monte_carlo(config, seed=42, trial_count=100)
    forecast = build_forecast(result, project_name="test")
    d = forecast_as_dict(forecast)
    import json
    json.dumps(d)
    assert "duration" in d
    assert "uncertainty" in d
    assert "trajectory" in d


# --- Scenarios ---

def test_run_what_if_with_scenarios():
    config = SimConfig(
        variables=[
            SimVariable(name="debt:top_item", dist=from_pessimistic(1.3, 0.2), category="debt"),
            SimVariable(
                name="failure:test", dist=Fixed(10.0), category="failure",
                is_failure_event=True, bernoulli_prob=0.3,
            ),
        ],
        trial_count=500,
        seed=42,
        base_duration_days=30.0,
    )
    scenarios = [
        Scenario(name="Fix top debt", description="Resolve top debt", disable_variables=["debt:top_item"]),
        Scenario(name="No failures", description="Zero failures", failure_probability_multiplier=0.0),
    ]
    report = run_what_if(config, scenarios, seed=42, trial_count=500)
    assert len(report.scenarios) == 2
    assert report.baseline is not None


def test_scenarios_from_risk():
    risk_data = {
        "debt_report": {
            "top_5": [{"item": "Test debt item", "priority": 5.0, "impact": 5, "probability": 0.5}],
        },
        "failure_report": {"modes": []},
    }
    scenarios = scenarios_from_risk(risk_data)
    assert len(scenarios) >= 4


def test_what_if_as_dict():
    config = SimConfig(variables=[], trial_count=100, seed=42, base_duration_days=30.0)
    report = run_what_if(config, [], seed=42, trial_count=100)
    d = what_if_as_dict(report)
    import json
    json.dumps(d)
    assert "baseline" in d
    assert "scenarios" in d


# --- Sensitivity ---

def test_sensitivity_ranks_variables():
    # Two variables with clearly different variance — wide Triangular vs tight PERT
    config = SimConfig(
        variables=[
            SimVariable(name="tight_var", dist=PERT(0.95, 1.05, 1.15), category="debt"),
            SimVariable(name="wide_var", dist=Triangular(0.5, 1.5, 3.0), category="debt"),
        ],
        trial_count=1000,
        seed=42,
        base_duration_days=30.0,
    )
    report = analyze_sensitivity(config, seed=42, trial_count=1000)
    assert len(report.items) == 2
    # wide_var should have larger absolute delta than tight_var
    wide_delta = abs(next(i.delta for i in report.items if i.variable == "wide_var"))
    tight_delta = abs(next(i.delta for i in report.items if i.variable == "tight_var"))
    assert wide_delta >= tight_delta, f"Wide var delta ({wide_delta}) should >= tight ({tight_delta})"


def test_sensitivity_as_dict():
    config = SimConfig(variables=[], trial_count=100, seed=42, base_duration_days=30.0)
    report = analyze_sensitivity(config, seed=42, trial_count=100)
    d = sensitivity_as_dict(report)
    import json
    json.dumps(d)
    assert "top_drivers" in d
    assert "items" in d