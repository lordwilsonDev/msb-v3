from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ContextChunk:
    source: str
    content: str
    metadata: Dict[str, str] = field(default_factory=dict)


class ContextEngine:
    def __init__(self) -> None:
        self.chunks: List[ContextChunk] = []
        self.index: Dict[str, int] = {}

    def ingest(self, chunk: ContextChunk) -> int:
        idx = len(self.chunks)
        self.chunks.append(chunk)
        self.index[chunk.source] = idx
        return idx

    def retrieve(self, source: str) -> Optional[ContextChunk]:
        idx = self.index.get(source)
        if idx is None:
            return None
        return self.chunks[idx]

    def search(self, term: str) -> List[ContextChunk]:
        lower = term.lower()
        return [c for c in self.chunks if lower in c.content.lower()]
