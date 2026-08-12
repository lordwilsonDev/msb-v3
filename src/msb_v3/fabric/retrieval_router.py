"""Retrieval domains (spec §3/§6 Phase 2, blueprint §4).

The blueprint's retrieval fabric is not one big vector DB — it's *domains*
(semantic memory, episodic memory, knowledge) behind a router. The existing
`retrieval.engine.RetrievalRouter` already does deterministic route planning
(vector/temporal/structural) over Qdrant; this module adds the *domain* layer
on top:

    domain -> route policy -> RetrievalRouter.run(routes=...)

Domains:
    knowledge  — documents/notes (vector + structural metadata routes)
    episodic   — events/recency (temporal route + runtime store recent runs)
    semantic   — pure vector similarity (the minimal grounding route)

Domain selection is deterministic (cue-based, zero LLM), same philosophy as
`retrieval.planner`. A task declares which domain it needs; the router
returns ranked, provenance-annotated matches, and any route that fails is
recorded (route_errors) without crashing the query (Phase 2 acceptance:
\"multi-word semantic query returns ranked grounded snippets\").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from msb_v3.retrieval.engine import RetrievalRouter

# Query cues that push a query into the episodic (recency/events) domain.
_EPISODIC_CUES = (
    "recent", "recently", "yesterday", "today", "last week", "this week",
    "last month", "this month", "last 90 days", "last 30 days", "last 7 days",
    "events", "event log", "what happened", "did we", "when did", "newest",
    "latest", "timeline",
)

# Route policies per domain. "all" = let the engine's own cue-based planner
# decide (vector + temporal + structural as cued). Explicit lists pin the
# route set with equal weights (engine.plan_explicit).
_DOMAIN_ROUTES: Dict[str, Optional[List[str]]] = {
    "knowledge": ["vector", "structural"],
    "episodic": ["temporal"],
    "semantic": ["vector"],
    "all": None,
}


@dataclass(frozen=True)
class DomainResult:
    domain: str
    query: str
    matches: List[Dict[str, Any]]
    route_errors: Dict[str, str]
    latency_ms: int


def detect_domain(query: str, *, declared: Optional[str] = None) -> str:
    """Pick the retrieval domain for a query.

    `declared` wins (the agent can state the domain from task context);
    otherwise the query's cues decide (deterministic). Unknown declared
    domains fall back to the cue-based detection.
    """
    if declared in _DOMAIN_ROUTES:
        return declared
    q = (query or "").lower()
    if any(cue in q for cue in _EPISODIC_CUES):
        return "episodic"
    return "knowledge"


class FabricRetrievalRouter:
    """Domain layer over the engine. Same tenant-scoped contract as
    RetrievalRouter, plus a domain declaration."""

    def __init__(self, tenant_id: str = "default") -> None:
        self.tenant_id = tenant_id
        self._engine = RetrievalRouter(tenant_id)

    async def run(
        self,
        query: str,
        *,
        domain: Optional[str] = None,
        top_k: int = 5,
    ) -> DomainResult:
        domain = detect_domain(query, declared=domain)
        routes = _DOMAIN_ROUTES[domain]
        result = await self._engine.run(query, top_k=top_k, routes=routes)
        return DomainResult(
            domain=domain,
            query=query,
            matches=result["matches"],
            route_errors=result["route_errors"],
            latency_ms=result["latency_ms"],
        )
