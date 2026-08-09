"""Weighted Reciprocal Rank Fusion (RRF).

Combines per-route ranked lists into a single ranking, weighting each route's
contribution by its plan weight: score = sum(weight / (k + rank)). Deterministic:
routes are consumed in sorted order and ties break on id ascending.
"""

from __future__ import annotations

from typing import Any

_K = 60


def rrf(ranked_lists: dict[str, list[dict]], weights: dict[str, float], k: int = _K) -> list[dict]:
    """Fuse {route: [records]} into a ranked list with provenance.

    Returns [{id, score, best: record, routes: [{route, rank, score}]}] sorted
    by fused score descending (ties broken by id ascending).
    """
    fused: dict[str, dict[str, Any]] = {}
    for route in sorted(ranked_lists):
        weight = float(weights.get(route, 1.0))
        for rank, record in enumerate(ranked_lists[route], start=1):
            doc_id = str(record["id"])
            entry = fused.setdefault(doc_id, {"id": doc_id, "best": record, "routes": []})
            contribution = weight / (k + rank)
            entry["score"] = entry.get("score", 0.0) + contribution
            entry["routes"].append({"route": route, "rank": rank, "score": round(contribution, 6)})
            # Keep the highest per-route score's record as the display record.
            if record["score"] > entry["best"].get("score", 0.0):
                entry["best"] = record

    ordered = sorted(fused.values(), key=lambda e: (-e["score"], e["id"]))
    for entry in ordered:
        entry["score"] = round(entry["score"], 6)
        entry["routes"].sort(key=lambda r: (r["route"], r["rank"]))
    return ordered
