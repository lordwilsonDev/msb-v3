"""Provenance binding for every assertion in a ProjectTwin.

Provenance levels (rising confidence):
    UNKNOWN      — nothing is known
    CLAIMED      — stated in documentation but unverified
    INFERRED     — deduced from evidence but not directly observed
    OBSERVED     — directly measured from live state
    VERIFIED     — independently confirmed (test/audit/replay)
    CONTRADICTED — evidence disagrees with the assertion

Every field in a ProjectTwin that carries a value also carries a provenance
tag and a *source* (file path, endpoint, command) so the reader can trace
back to what produced the assertion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Provenance(StrEnum):
    """How an assertion is known — rising confidence."""

    UNKNOWN = "UNKNOWN"
    CLAIMED = "CLAIMED"
    INFERRED = "INFERRED"
    OBSERVED = "OBSERVED"
    VERIFIED = "VERIFIED"
    CONTRADICTED = "CONTRADICTED"


@dataclass(slots=True)
class Provenanced:
    """A value with its provenance tag and source."""

    value: Any
    provenance: Provenance = Provenance.UNKNOWN
    source: str = ""  # file path, endpoint, or command that produced this

    @classmethod
    def unknown(cls) -> "Provenanced":
        return cls(value=None, provenance=Provenance.UNKNOWN)

    @classmethod
    def claimed(cls, value: Any, source: str = "") -> "Provenanced":
        return cls(value=value, provenance=Provenance.CLAIMED, source=source)

    @classmethod
    def inferred(cls, value: Any, source: str = "") -> "Provenanced":
        return cls(value=value, provenance=Provenance.INFERRED, source=source)

    @classmethod
    def observed(cls, value: Any, source: str = "") -> "Provenanced":
        return cls(value=value, provenance=Provenance.OBSERVED, source=source)

    @classmethod
    def verified(cls, value: Any, source: str = "") -> "Provenanced":
        return cls(value=value, provenance=Provenance.VERIFIED, source=source)

    @classmethod
    def contradicted(cls, value: Any, source: str = "") -> "Provenanced":
        return cls(value=value, provenance=Provenance.CONTRADICTED, source=source)

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "provenance": self.provenance.value, "source": self.source}