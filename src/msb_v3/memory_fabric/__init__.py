"""Memory Fabric (Sovereign Architecture v4.0 §4.2.2, P0).

Persistent, provenance-tracked agent memory across sessions. Four memory
types (episodic / semantic / procedural / architectural), a verification
state machine (UNVERIFIED → VERIFIED → CONTRADICTED → DEPRECATED), a
decay model (confidence(t) = importance × recency × contradiction — spec
§17), and soft-delete forget + consolidation.

Storage is plain SQLite (zero new dependencies, the same choice as the
Code Graph): a memory_items table with full provenance columns and a
verification_history table recording every state transition (who, when,
why). Recall is deterministic SQLite scoring (keyword/tag/project/type
filters × importance × recency), with a best-effort semantic boost via
the existing Qdrant embedding path when configured — the boost never
breaks recall.

Honest scope: this is durable agent memory with provenance and decay,
not a vector store of its own. Semantic ranking rides the existing
Qdrant RAG seam; the SQLite score is always available offline.
"""

from msb_v3.memory_fabric.fabric import MemoryFabric, MemoryRecall
from msb_v3.memory_fabric.models import MemoryItem, MemoryType, VerificationState
from msb_v3.memory_fabric.store import MemoryFabricStore

__all__ = [
    "MemoryFabric",
    "MemoryFabricStore",
    "MemoryItem",
    "MemoryRecall",
    "MemoryType",
    "VerificationState",
]
