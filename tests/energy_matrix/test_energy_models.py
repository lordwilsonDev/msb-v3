"""Tests for EnergyMatrix data models."""

from __future__ import annotations

from msb_v3.energy_matrix.models import EnergyBudget, ResourceDecision, SystemTelemetry


def test_telemetry_defaults() -> None:
    t = SystemTelemetry()
    assert t.cpu_percent == 0.0
    assert t.ram_percent == 0.0
    assert t.disk_percent == 0.0


def test_telemetry_to_dict() -> None:
    t = SystemTelemetry(cpu_percent=50.0, ram_percent=70.0, disk_percent=40.0)
    d = t.to_dict()
    assert d["cpu_percent"] == 50.0
    assert d["ram_percent"] == 70.0
    assert "timestamp" in d


def test_decision_defaults() -> None:
    d = ResourceDecision()
    assert d.action == "run"
    assert d.confidence == 1.0


def test_decision_to_dict() -> None:
    d = ResourceDecision(action="defer", reason="busy", confidence=0.8)
    r = d.to_dict()
    assert r["action"] == "defer"
    assert r["reason"] == "busy"


def test_budget_defaults() -> None:
    b = EnergyBudget()
    assert b.max_cpu_percent == 85.0
    assert b.max_ram_percent == 85.0
    assert b.max_disk_percent == 90.0


def test_budget_custom() -> None:
    b = EnergyBudget(max_cpu_percent=70.0, defer_cpu_percent=50.0)
    assert b.max_cpu_percent == 70.0
    assert b.defer_cpu_percent == 50.0
