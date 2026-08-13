"""Intelligence Fabric (spec §3 Layer 2, Phase 2).

The hybrid routing + retrieval + context layer that makes the slice's brain
and grounding composable:

    model_router      hybrid local/frontier routing with the R score (spec §3.5)
    retrieval_router  retrieval domains (semantic/episodic/knowledge) over Qdrant
    context           token-budgeted context builder with deterministic eviction

All three are deterministic where possible (models propose, code governs)
and unit-testable without live services.
"""

from msb_v3.fabric.context import BuiltContext, ContextBuilder, ContextLedger
from msb_v3.fabric.model_router import (
    DEFAULT_TIER,
    FRONTIER_THRESHOLD,
    WEIGHTS,
    ModelRouter,
    RouterDecision,
)
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
    "FRONTIER_THRESHOLD",
    "ModelRouter",
    "RouterDecision",
    "WEIGHTS",
    "detect_domain",
]
