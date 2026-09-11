"""Intelligence Fabric (spec §3 Layer 2).

The routing + retrieval + context layer that makes the slice's brain
and grounding composable:

    model_router      deterministic local-only routing (frontier retired 2026-09-09)
    retrieval_router  retrieval domains (semantic/episodic/knowledge) over Qdrant
    context           token-budgeted context builder with deterministic eviction

All three are deterministic where possible (models propose, code governs)
and unit-testable without live services.
"""

from msb_v3.fabric.context import BuiltContext, ContextBuilder, ContextLedger
from msb_v3.fabric.model_router import DEFAULT_TIER, ModelRouter, RouterDecision
from msb_v3.fabric.retrieval_router import (
    DomainResult,
    FabricRetrievalRouter,
    detect_domain,
)

__all__ = [
    "BuiltContext",
    "ContextBuilder",
    "ContextLedger",
    "DEFAULT_TIER",
    "DomainResult",
    "FabricRetrievalRouter",
    "ModelRouter",
    "RouterDecision",
    "detect_domain",
]
