"""Tests for EnergyMatrix scheduler decision logic."""

from __future__ import annotations
import pytest

pytest.importorskip("psutil", reason="psutil not installed")


from msb_v3.energy_matrix.models import EnergyBudget, SystemTelemetry
from msb_v3.energy_matrix.scheduler import decide, should_run_task


def _make_telemetry(cpu: float = 50.0, ram: float = 60.0, disk: float = 40.0, temp: float = 0.0) -> SystemTelemetry:
    return SystemTelemetry(
        cpu_percent=cpu,
        cpu_cores=8,
        ram_total_gb=16.0,
        ram_used_gb=16.0 * ram / 100,
        ram_percent=ram,
        disk_total_gb=228.0,
        disk_used_gb=228.0 * disk / 100,
        disk_percent=disk,
        temperature_c=temp,
    )


def test_decide_normal_system() -> None:
    t = _make_telemetry(cpu=30, ram=50, disk=40)
    d = decide(telemetry=t)
    assert d.action == "run"
    assert d.telemetry is not None


def test_decide_high_cpu_defer() -> None:
    t = _make_telemetry(cpu=75, ram=50, disk=40)
    budget = EnergyBudget(defer_cpu_percent=70, max_cpu_percent=85)
    d = decide(telemetry=t, budget=budget)
    assert d.action == "defer"
    assert "CPU" in d.reason


def test_decide_critical_cpu_skip() -> None:
    t = _make_telemetry(cpu=90, ram=50, disk=40)
    budget = EnergyBudget(max_cpu_percent=85)
    d = decide(telemetry=t, budget=budget)
    assert d.action == "skip"
    assert "CPU" in d.reason


def test_decide_high_ram_skip() -> None:
    t = _make_telemetry(cpu=30, ram=90, disk=40)
    d = decide(telemetry=t)
    assert d.action == "skip"
    assert "RAM" in d.reason


def test_decide_high_disk_skip() -> None:
    t = _make_telemetry(cpu=30, ram=50, disk=95)
    d = decide(telemetry=t)
    assert d.action == "skip"
    assert "Disk" in d.reason


def test_decide_normal_temp() -> None:
    t = _make_telemetry(cpu=30, ram=50, disk=40, temp=45.0)
    d = decide(telemetry=t)
    assert d.action == "run"


def test_decide_hot_temp_skip() -> None:
    t = _make_telemetry(cpu=30, ram=50, disk=40, temp=90.0)
    d = decide(telemetry=t)
    assert d.action == "skip"
    assert "Temperature" in d.reason


def test_decide_no_temp_skips_check() -> None:
    t = _make_telemetry(cpu=30, ram=50, disk=40, temp=0.0)
    d = decide(telemetry=t)
    assert d.action == "run"


def test_should_run_task_adds_context() -> None:
    t = _make_telemetry(cpu=30, ram=50, disk=40)
    d = should_run_task("ollama_inference", telemetry=t)
    assert d.action == "run"
    assert "ollama_inference" in d.reason


def test_decide_uses_real_telemetry() -> None:
    d = decide()
    assert d.action in ("run", "defer", "skip")
    assert d.telemetry is not None
    assert d.telemetry.cpu_percent >= 0


def test_decide_worst_wins() -> None:
    # CPU is fine, RAM is critical — should skip
    t = _make_telemetry(cpu=30, ram=90, disk=40)
    d = decide(telemetry=t)
    assert d.action == "skip"
