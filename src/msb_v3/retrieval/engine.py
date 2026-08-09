"""RetrievalRouter — parallel multi-index dispatch + fusion + provenance.

The blueprint's core loop, on-stack: plan (deterministic) -> parallel
dispatch to the selected index adapters -> RRF fusion -> provenance-annotated
context. A failing route degrades gracefully (recorded in route_errors), never
crashes the whole query.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from msb_v3.retrieval.fusion import rrf
from msb_v3.retrieval.indexes import get_adapter
from msb_v3.retrieval.planner import plan_query


class RetrievalRouter:
    def __init__(self, tenant_id: str = "default"):
        self.tenant_id = tenant_id

    async def run(self, query: str, top_k: int = 5) -> dict[str, Any]:
        started = time.perf_counter()
        plan = plan_query(query, top_k)
        weights = {r["index"]: r["weight"] for r in plan["routes"]}

        results_by_route: dict[str, list[dict]] = {}
        route_errors: dict[str, str] = {}

        async def _dispatch(route: dict) -> None:
            try:
                adapter = get_adapter(route["index"], self.tenant_id)
                results_by_route[route["index"]] = await adapter.search(
                    query, top_k=route["top_k"],
                )
            except Exception as exc:  # noqa: BLE001 — degrade, don't crash
                route_errors[route["index"]] = str(exc)

        await asyncio.gather(*(_dispatch(r) for r in plan["routes"]))

        fused = rrf(results_by_route, weights)
        matches: list[dict[str, Any]] = []
        for item in fused[: max(top_k, 1)]:
            matches.append({
                "score": item["score"],
                "source": item["best"].get("source", ""),
                "text": item["best"].get("text", ""),
                "metadata": item["best"].get("metadata"),
                "provenance": item["routes"],
            })

        return {
            "query": query,
            "plan": plan,
            "matches": matches,
            "context": {"tenant_id": self.tenant_id},
            "route_errors": route_errors,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
