"""MoIE evidence merger (spec §24, §31 item 21).

Grounds the expert analysis with independent evidence: a best-effort
memory-fabric recall against the claim (the same deterministic keyword
path the fabric already provides). A retrieval failure returns no
evidence and never breaks the analysis — IDS reports the honest count.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

Retriever = Callable[[str], List[Dict[str, Any]]]


def default_retriever(tenant: str = "default", top_k: int = 5) -> Retriever:
    """Best-effort memory-fabric recall. Returns a list of
    ``{memory_id, score, content}`` dicts; [] on any failure."""

    def _retrieve(claim: str) -> List[Dict[str, Any]]:
        try:
            from msb_v3.core.config import settings
            from msb_v3.memory_fabric.fabric import MemoryFabric
            from msb_v3.memory_fabric.store import MemoryFabricStore

            fabric = MemoryFabric(MemoryFabricStore(settings.memory_fabric_db_path))
            hits = fabric.recall_memories(claim, tenant=tenant, top_k=top_k)
            return [
                {"memory_id": h.memory_id, "score": h.score, "content": h.content[:120]}
                for h in hits
            ]
        except Exception:  # noqa: BLE001 — evidence is best-effort
            return []

    return _retrieve


def retrieve_evidence(
    claim: str,
    retriever: Optional[Retriever] = None,
    tenant: str = "default",
) -> List[Dict[str, Any]]:
    """Run the retriever (injected or default) and return the hits."""
    if retriever is None:
        retriever = default_retriever(tenant=tenant)
    try:
        hits = retriever(claim) or []
    except Exception:  # noqa: BLE001 — never break the analysis for evidence
        hits = []
    return hits
