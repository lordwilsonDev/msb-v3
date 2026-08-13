"""Sovereign Runtime — Agent Identity.

Creates a deterministic, versioned identity for the runtime.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class AgentIdentity:
    id: str = "sovereign-agent-001"
    # Must track msb_v3.__version__ (test_runtime_boot pins the equality) —
    # bump both together on release.
    version: str = "0.2.0"
    runtime: str = "msb-v3"
    host: str = field(default_factory=lambda: os.getenv("SOVEREIGN_HOST", "127.0.0.1"))
    environment: str = field(default_factory=lambda: os.getenv("SOVEREIGN_ENV", "development"))

    def to_dict(self) -> dict:
        return asdict(self)


identity = AgentIdentity()
