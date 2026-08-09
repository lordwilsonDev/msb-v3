"""Sovereign Runtime — Agent Identity.

Creates a deterministic, versioned identity for the runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict, field
from typing import Optional


@dataclass(frozen=True)
class AgentIdentity:
    id: str = "sovereign-agent-001"
    version: str = "0.1.0"
    runtime: str = "msb-v3"
    host: str = field(default_factory=lambda: os.getenv("SOVEREIGN_HOST", "127.0.0.1"))
    environment: str = field(default_factory=lambda: os.getenv("SOVEREIGN_ENV", "development"))

    def to_dict(self) -> dict:
        return asdict(self)


identity = AgentIdentity()
