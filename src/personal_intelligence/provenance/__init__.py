from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class ProvenanceEntry:
    key: str
    value: Any
    verified: bool = False
    confidence: float = 0.0
    source: str = "unknown"
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MemoryLedger:
    def __init__(self) -> None:
        self.entries: Dict[str, ProvenanceEntry] = {}

    def record(self, entry: ProvenanceEntry) -> None:
        self.entries[entry.key] = entry

    def promote(self, key: str, confidence: float, source: str) -> None:
        entry = self.entries.get(key)
        if entry is None:
            raise KeyError(key)
        entry.confidence = max(entry.confidence, confidence)
        entry.source = source
        entry.verified = True

    def summary(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for entry in self.entries.values():
            out.append(
                {
                    "key": entry.key,
                    "verified": entry.verified,
                    "confidence": entry.confidence,
                    "source": entry.source,
                    "recorded_at": entry.recorded_at,
                }
            )
        return sorted(out, key=lambda item: item["confidence"], reverse=True)
