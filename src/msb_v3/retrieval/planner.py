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
    "last quarter", "this quarter", "last year", "recent", "recently",
    "last 7 days", "last 30 days", "last 90 days",
    "since 2024", "since 2025", "since 2026", "from 2024", "from 2025",
)
# NOTE: q1-q4 are deliberately NOT temporal cues — "q2 filing" claims a
# period, not recency. The r02 outcome gate measured that firing the temporal
# route on such queries fuses recent unrelated docs above the exact match
# (router NDCG 1.0 -> 0.301 on the q2 case), so they were removed.

STRUCTURAL_CUES = (
    "tagged", "tag:", "tags:", "folder:", "folder ", "frontmatter",
    "metadata", "author:", "category:", "type:", "by author",
)

VECTOR_WEIGHT = 0.5
SECONDARY_WEIGHT = 0.35


def _finish(routes: list[dict]) -> dict:
    """Attach the plan-level flags (blueprint §2.2 plan shape)."""
    return {
        "routes": routes,
        "rerank": False,   # P1: cross-encoder rerank (not in P0)
        "verify": False,   # P1: verification step (not in P0)
        "provenance": True,
    }


def _normalize_weights(routes: list[dict]) -> list[dict]:
    """Normalize route weights to sum to exactly 1.0 (last-route correction)."""
    total = sum(r["weight"] for r in routes)
    for route in routes:
        route["weight"] = round(route["weight"] / total, 4)
    routes[-1]["weight"] = round(1.0 - sum(r["weight"] for r in routes[:-1]), 4)
    return routes


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

    return _finish(_normalize_weights(routes))


def plan_explicit(routes: list[str], top_k: int = 5) -> dict:
    """Build a plan from an explicit route set (equal weights).

    Used by the outcome gate to compare the full cue-based plan against a
    forced single-index baseline (e.g. routes=["vector"]) — the blueprint's
    "NDCG@10 over vector-only baseline" measurement. All routes get the
    plan_query vector budget (top_k * 2) so the comparison is apples-to-apples
    on candidate pool size.
    """
    if not routes:
        raise ValueError("plan_explicit: routes must be non-empty")
    weight = 1.0 / len(routes)
    built = [
        {"index": name, "weight": weight, "top_k": max(top_k, 1) * 2}
        for name in routes
    ]
    return _finish(_normalize_weights(built))
