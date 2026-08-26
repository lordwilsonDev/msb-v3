"""Data models for EnergyMatrix telemetry and scheduling."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SystemTelemetry:
    """Snapshot of system resource state."""

    # CPU
    cpu_percent: float = 0.0
    cpu_cores: int = 0
    cpu_freq_mhz: float = 0.0

    # Memory
    ram_total_gb: float = 0.0
    ram_used_gb: float = 0.0
    ram_percent: float = 0.0

    # Disk
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_percent: float = 0.0

    # Temperature (if available)
    temperature_c: float = 0.0

    # Power (if available)
    power_watts: float = 0.0

    # Timestamps
    timestamp: str = ""
    sample_duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu_percent": self.cpu_percent,
            "cpu_cores": self.cpu_cores,
            "ram_percent": self.ram_percent,
            "ram_used_gb": round(self.ram_used_gb, 1),
            "ram_total_gb": round(self.ram_total_gb, 1),
            "disk_percent": self.disk_percent,
            "temperature_c": self.temperature_c,
            "power_watts": self.power_watts,
            "timestamp": self.timestamp,
        }


@dataclass
class ResourceDecision:
    """A scheduling decision based on telemetry."""

    action: str = "run"  # run, defer, skip
    reason: str = ""
    confidence: float = 1.0
    telemetry: Optional[SystemTelemetry] = None
    thresholds_used: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "confidence": self.confidence,
            "thresholds": self.thresholds_used,
        }


@dataclass
class EnergyBudget:
    """Resource limits for the system."""

    max_cpu_percent: float = 85.0
    max_ram_percent: float = 85.0
    max_disk_percent: float = 90.0
    max_temperature_c: float = 85.0
    defer_cpu_percent: float = 70.0
    defer_ram_percent: float = 75.0
