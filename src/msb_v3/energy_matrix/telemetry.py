"""System telemetry reader.

Reads Apple Silicon resource state using psutil (CPU, RAM, disk)
and system tools for temperature/power where available.

No external dependencies beyond psutil (already installed).
No sudo required for basic metrics.
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone

from msb_v3.energy_matrix.models import SystemTelemetry


def read_tlemetry(sample_duration_ms: float = 100) -> SystemTelemetry:
    """Read current system resource state.

    Args:
        sample_duration_ms: How long to sample CPU (ms). Default 100ms.

    Returns:
        SystemTelemetry with current resource usage.
    """
    import psutil

    start = time.monotonic()

    # CPU
    cpu_percent = psutil.cpu_percent(interval=sample_duration_ms / 1000)
    cpu_count = psutil.cpu_count(logical=True) or 1
    cpu_freq = psutil.cpu_freq()
    freq_mhz = cpu_freq.current if cpu_freq else 0.0

    # Memory
    mem = psutil.virtual_memory()

    # Disk
    disk = psutil.disk_usage("/")

    # Temperature (best effort — may need sudo)
    temp = _read_temperature()

    # Power (best effort — not always available)
    power = _read_power()

    elapsed = (time.monotonic() - start) * 1000

    return SystemTelemetry(
        cpu_percent=cpu_percent,
        cpu_cores=cpu_count,
        cpu_freq_mhz=freq_mhz,
        ram_total_gb=mem.total / (1024**3),
        ram_used_gb=mem.used / (1024**3),
        ram_percent=mem.percent,
        disk_total_gb=disk.total / (1024**3),
        disk_used_gb=disk.used / (1024**3),
        disk_percent=disk.percent,
        temperature_c=temp,
        power_watts=power,
        timestamp=datetime.now(timezone.utc).isoformat(),
        sample_duration_ms=elapsed,
    )


def _read_temperature() -> float:
    """Read CPU temperature. Returns 0.0 if unavailable."""
    try:
        result = subprocess.run(
            ["osx-cpu-temp"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            text = result.stdout.strip()
            # Parse "45.0°C" format
            value = float(text.replace("°C", "").replace("C", "").strip())
            return value
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return 0.0


def _read_power() -> float:
    """Read system power consumption. Returns 0.0 if unavailable."""
    # psutil doesn't expose power on macOS directly
    # We could use IOReport via ctypes, but for v0.1 return 0.0
    return 0.0


def read_telemetry_fast() -> SystemTelemetry:
    """Read telemetry with minimal sampling (0ms CPU interval).

    Use when you need a quick snapshot without blocking.
    CPU percent will be less accurate but the call returns instantly.
    """
    return read_tlemetry(sample_duration_ms=0)
