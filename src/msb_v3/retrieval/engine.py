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
from msb_v3.retrieval.planner import plan_explicit, plan_query
from msb_v3.secrets.redact import redact, redact_obj


class RetrievalRouter:
    def __init__(self, tenant_id: str = "default"):
        self.tenant_id = tenant_id

    async def run(
        self, query: str, top_k: int = 5, routes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run the retrieval plan for a query.

        routes=None -> the cue-based plan (normal path). routes=[...] forces
        an explicit route set with equal weights — used by the outcome gate to
        measure the single-index baseline vs the full plan.
        """
        started = time.perf_counter()
        plan = plan_query(query, top_k) if routes is None else plan_explicit(routes, top_k)
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
                # An adapter's exception string is free text (it often quotes a
                # URL or a query) and it is returned to the caller, so it is
                # redacted like any other output.
                route_errors[route["index"]] = redact(str(exc))

        await asyncio.gather(*(_dispatch(r) for r in plan["routes"]))

        fused = rrf(results_by_route, weights)
        matches: list[dict[str, Any]] = []
        for item in fused[: max(top_k, 1)]:
            matches.append({
                "id": item["best"].get("id", ""),
                "score": item["score"],
                "source": item["best"].get("source", ""),
                # The *RAG* channel: retrieved text and metadata are handed to
                # the model as context and returned to the caller, so they are
                # redacted at the assembly point, once, for both consumers.
                "text": redact(item["best"].get("text", "")),
                "metadata": redact_obj(item["best"].get("metadata")),
                "provenance": item["routes"],
            })

        return {
            # A query is echoed back and fed to the model; if someone pastes a
            # credential into a search box, this is where it would travel.
            "query": redact(query),
            "plan": plan,
            "matches": matches,
            "context": {"tenant_id": self.tenant_id},
            "route_errors": route_errors,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
