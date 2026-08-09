"""Deterministic retrieval planner: query -> weighted multi-index plan.

Zero-LLM by design: for a small fixed set of index routes, a feature-based
planner is deterministic, unit-testable, and free. The vector route always
participates; the temporal and structural routes join when the query carries
their cues. Route weights are normalized to sum to exactly 1.0.

Plan shape (blueprint §2.2):
    {"routes": [{"index": str, "weight": float, "top_k": int}],
     "rerank": bool, "verify": bool, "provenance": bool}
"""

from __future__ import annotations

TEMPORAL_CUES = (
    "today", "yesterday", "last week", "this week", "last month", "this month",
    "last year", "recent", "recently", "last 7 days", "last 30 days",
    "last 90 days", "q1", "q2", "q3", "q4", "since 2024", "since 2025",
    "since 2026", "from 2024", "from 2025",
)

STRUCTURAL_CUES = (
    "tagged", "tag:", "tags:", "folder:", "folder ", "frontmatter",
    "metadata", "author:", "category:", "type:", "by author",
)

VECTOR_WEIGHT = 0.5
SECONDARY_WEIGHT = 0.35


def plan_query(query: str, top_k: int = 5) -> dict:
    """Build the weighted retrieval plan for a query (deterministic)."""
    q = (query or "").lower()
    routes: list[dict] = [
        {"index": "vector", "weight": VECTOR_WEIGHT, "top_k": max(top_k, 1) * 2},
    ]
    if any(cue in q for cue in TEMPORAL_CUES):
        routes.append({"index": "temporal", "weight": SECONDARY_WEIGHT, "top_k": max(top_k, 1)})
    if any(cue in q for cue in STRUCTURAL_CUES):
        routes.append({"index": "structural", "weight": SECONDARY_WEIGHT, "top_k": max(top_k, 1)})

    total = sum(r["weight"] for r in routes)
    for route in routes:
        route["weight"] = round(route["weight"] / total, 4)
    # Exact-sum correction so weights always add to 1.0.
    routes[-1]["weight"] = round(1.0 - sum(r["weight"] for r in routes[:-1]), 4)

    return {
        "routes": routes,
        "rerank": False,   # P1: cross-encoder rerank (not in P0)
        "verify": False,   # P1: verification step (not in P0)
        "provenance": True,
    }
