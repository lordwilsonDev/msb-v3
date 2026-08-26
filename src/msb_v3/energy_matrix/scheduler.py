"""Resource-aware scheduler.

Makes simple decisions based on system telemetry:
- RUN: system has capacity, proceed
- DEFER: system is busy, wait and retry
- SKIP: system is overloaded, don't even try

This is NOT autonomous — every decision is logged and can be
overridden by the operator or ActionGate.
"""

from __future__ import annotations

from typing import Optional

from msb_v3.energy_matrix.models import EnergyBudget, ResourceDecision, SystemTelemetry
from msb_v3.energy_matrix.telemetry import read_telemetry_fast


def decide(
    telemetry: Optional[SystemTelemetry] = None,
    budget: Optional[EnergyBudget] = None,
) -> ResourceDecision:
    """Make a scheduling decision based on current system state.

    Args:
        telemetry: System state snapshot. None = read fresh.
        budget: Resource limits. None = use defaults.

    Returns:
        ResourceDecision with action (run/defer/skip) and reason.
    """
    if telemetry is None:
        telemetry = read_telemetry_fast()

    if budget is None:
        budget = EnergyBudget()

    # Check each resource against budget
    checks = [
        _check_cpu(telemetry, budget),
        _check_ram(telemetry, budget),
        _check_disk(telemetry, budget),
        _check_temperature(telemetry, budget),
    ]

    # Find the worst verdict
    verdicts = [c.action for c in checks]

    if "skip" in verdicts:
        # System is overloaded — skip
        worst = next(c for c in checks if c.action == "skip")
        return ResourceDecision(
            action="skip",
            reason=worst.reason,
            confidence=worst.confidence,
            telemetry=telemetry,
            thresholds_used=worst.thresholds,
        )

    if "defer" in verdicts:
        # System is busy — defer
        worst = next(c for c in checks if c.action == "defer")
        return ResourceDecision(
            action="defer",
            reason=worst.reason,
            confidence=worst.confidence,
            telemetry=telemetry,
            thresholds_used=worst.thresholds,
        )

    # All clear — run
    return ResourceDecision(
        action="run",
        reason="system has capacity",
        confidence=1.0,
        telemetry=telemetry,
        thresholds_used={},
    )


def should_run_task(
    task_name: str = "unknown",
    telemetry: Optional[SystemTelemetry] = None,
    budget: Optional[EnergyBudget] = None,
) -> ResourceDecision:
    """Check if a specific task should run now.

    Convenience wrapper around decide() that adds task context.
    """
    decision = decide(telemetry=telemetry, budget=budget)
    decision.reason = f"[{task_name}] {decision.reason}"
    return decision


# ── Individual resource checks ──────────────────────────────────────────


class _CheckResult:
    """Internal result from a resource check."""

    __slots__ = ("action", "reason", "confidence", "thresholds")

    def __init__(
        self,
        action: str = "run",
        reason: str = "",
        confidence: float = 1.0,
        thresholds: Optional[dict] = None,
    ) -> None:
        self.action = action
        self.reason = reason
        self.confidence = confidence
        self.thresholds = thresholds or {}


def _check_cpu(telemetry: SystemTelemetry, budget: EnergyBudget) -> _CheckResult:
    """Check CPU against budget."""
    cpu = telemetry.cpu_percent

    if cpu >= budget.max_cpu_percent:
        return _CheckResult(
            action="skip",
            reason=f"CPU at {cpu:.0f}% (limit {budget.max_cpu_percent:.0f}%)",
            confidence=0.9,
            thresholds={"max": budget.max_cpu_percent, "current": cpu},
        )

    if cpu >= budget.defer_cpu_percent:
        return _CheckResult(
            action="defer",
            reason=f"CPU at {cpu:.0f}% (defer threshold {budget.defer_cpu_percent:.0f}%)",
            confidence=0.8,
            thresholds={"defer": budget.defer_cpu_percent, "current": cpu},
        )

    return _CheckResult(action="run", thresholds={"current": cpu})


def _check_ram(telemetry: SystemTelemetry, budget: EnergyBudget) -> _CheckResult:
    """Check RAM against budget."""
    ram = telemetry.ram_percent

    if ram >= budget.max_ram_percent:
        return _CheckResult(
            action="skip",
            reason=f"RAM at {ram:.0f}% ({telemetry.ram_used_gb:.1f}GB / {telemetry.ram_total_gb:.1f}GB)",
            confidence=0.95,
            thresholds={"max": budget.max_ram_percent, "current": ram},
        )

    if ram >= budget.defer_ram_percent:
        return _CheckResult(
            action="defer",
            reason=f"RAM at {ram:.0f}% (defer threshold {budget.defer_ram_percent:.0f}%)",
            confidence=0.8,
            thresholds={"defer": budget.defer_ram_percent, "current": ram},
        )

    return _CheckResult(action="run", thresholds={"current": ram})


def _check_disk(telemetry: SystemTelemetry, budget: EnergyBudget) -> _CheckResult:
    """Check disk against budget."""
    disk = telemetry.disk_percent

    if disk >= budget.max_disk_percent:
        return _CheckResult(
            action="skip",
            reason=f"Disk at {disk:.0f}% (limit {budget.max_disk_percent:.0f}%)",
            confidence=0.95,
            thresholds={"max": budget.max_disk_percent, "current": disk},
        )

    return _CheckResult(action="run", thresholds={"current": disk})


def _check_temperature(telemetry: SystemTelemetry, budget: EnergyBudget) -> _CheckResult:
    """Check temperature against budget."""
    temp = telemetry.temperature_c

    if temp <= 0:
        # Temperature unavailable — skip check
        return _CheckResult(action="run")

    if temp >= budget.max_temperature_c:
        return _CheckResult(
            action="skip",
            reason=f"Temperature at {temp:.0f}°C (limit {budget.max_temperature_c:.0f}°C)",
            confidence=0.9,
            thresholds={"max": budget.max_temperature_c, "current": temp},
        )

    return _CheckResult(action="run", thresholds={"current": temp})
