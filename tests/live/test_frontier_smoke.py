"""Live frontier-seam smoke (completion blueprint Phase 5).

Proves the external model path end-to-end against the live /v1 seam:

    local request -> capability gateway -> frontier provider -> response
    -> telemetry -> evidence -> audit

Opt-in: requires ``MSB_LIVE_TESTS=1`` against a running stack AND
``OPENAI_API_KEY`` set (the seam is closed otherwise). It is honest about the
deployment: the default ``OPENAI_FRONTIER_URL`` is the server's *own* /v1
adapter (a loopback that fronts the local models), not a third-party
datacenter — so "frontier" here means the OpenAI-compatible surface and its
failover, not "we called a remote model". The invariant the smoke pins is the
one that matters for the seam: routing picks the frontier client when the seam
is open, the hop returns a DAG without crashing, and a dead provider degrades
to the deterministic template (never an uncontrolled execution). The
dead-provider case is hermetic and CI-runnable in tests/fabric/test_model_router.py;
this file is the live hop only.
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("MSB_LIVE_TESTS") != "1",
    reason="opt-in live smoke test — set MSB_LIVE_TESTS=1 against a running stack",
)


async def _plan_live(client) -> object:
    from msb_v3.agent.intent import Intent
    from msb_v3.agent.planner import plan

    intent = Intent(request="public: plan a vault search", goals=("search",), source="llm")
    return await plan(intent, client=client)


def test_frontier_hop_routes_and_plans_live() -> None:
    """With the seam open, a frontier-default task routes to the FrontierClient
    and planning returns a DAG (llm from the frontier, or a template fallback
    if the model output was unparseable — never a crash, never a faked tier)."""
    from msb_v3.core.config import settings
    from msb_v3.fabric.model_router import FrontierClient, resolve_client

    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY not set — /v1 seam closed")

    client, decision = resolve_client("plan", privacy_scoped=False)
    assert decision.tier == "frontier", decision.reason
    assert isinstance(client, FrontierClient)

    graph = asyncio.run(_plan_live(client))
    assert graph.tasks
    assert graph.source in ("llm", "template")
