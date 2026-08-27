"""Tests for EnergyMatrix telemetry reader."""

from __future__ import annotations

import pytest

pytest.importorskip("psutil", reason="psutil not installed")


from msb_v3.energy_matrix.telemetry import read_telemetry_fast, read_tlemetry


def test_read_telemetry_returns_populated() -> None:
    t = read_tlemetry(sample_duration_ms=0)
    assert t.cpu_percent >= 0
    assert t.cpu_cores > 0
    assert t.ram_total_gb > 0
    assert t.ram_used_gb > 0
    assert t.ram_percent > 0
    assert t.disk_total_gb > 0
    assert t.timestamp != ""


def test_read_telemetry_fast() -> None:
    t = read_telemetry_fast()
    assert t.cpu_percent >= 0
    assert t.ram_percent > 0
    assert t.sample_duration_ms < 100  # Should be very fast


def test_telemetry_has_disk() -> None:
    t = read_telemetry_fast()
    assert t.disk_percent > 0
    assert t.disk_total_gb > 0


def test_telemetry_to_dict_roundtrip() -> None:
    t = read_telemetry_fast()
    d = t.to_dict()
    assert "cpu_percent" in d
    assert "ram_percent" in d
    assert "disk_percent" in d
    assert "timestamp" in d
