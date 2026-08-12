"""Handle-this — the Dream Big Blue vertical slice (North Star, blueprint §20).

    handle("research the vault and write a brief about X")

runs the full loop end-to-end on the live msb-v3 surfaces:
    interpret (intent) -> plan (task DAG) -> safe-execute (gated tools,
    grounded verification) -> evidence (trace into the UAC audit chain).

Safety semantics (A8): a write derived from untrusted content (tainted) is
REVIEW-gated unless the operator pre-approved the plan (`approve=True`,
authorizing the intent's declared permissions). Every run produces a trace
with a deterministic_hash — same input, same evidence (the slice's replay
gate). No LLM judge anywhere in the verification path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from msb_v3.agent.bridge_provider import BridgeProvider
from msb_v3.agent.executor import ToolProvider, execute_graph
from msb_v3.agent.intent import Intent, interpret_intent
from msb_v3.agent.planner import plan
from msb_v3.agent.safety import ActionGate, SafeProvider
from msb_v3.agent.trace import AgentTrace, build_trace, record_trace
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.local_ai.llama_client import LlamaCPPClient
from msb_v3.local_ai.ollama import LocalAIClient


@dataclass
class HandleResult:
    ok: bool
    run_id: str
    verdict: str  # PASS | FAIL | ERROR
    deterministic_hash: str = ""
    trace: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


def _run_id(request: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"dbb-{stamp}-{abs(hash(request)) % 100000:05d}"


async def handle(
    request: str,
    *,
    client: LocalAIClient | LlamaCPPClient | None = None,
    tenant: str = "wilson-vault",
    approve: bool = False,
    output_dir: str | None = None,
    session: str = "default",
    provider: ToolProvider | None = None,
    gate: ActionGate | None = None,
) -> HandleResult:
    """Run the slice end-to-end. Returns the HandleResult (ok + evidence)."""
    request = (request or "").strip()
    if not request:
        return HandleResult(ok=False, run_id="", verdict="ERROR", error="empty request")

    run_id = _run_id(request)

    try:
        intent: Intent = interpret_intent(request, client=client)
        graph = plan(intent, client=client)

        # Approved capabilities: the operator pre-authorizes the declared
        # permissions (default: none — tainted writes then require review).
        approved = set(intent.permissions) if approve else set()

        if gate is None:
            gate = ActionGate(killswitch=KillSwitch())
        if provider is None:
            provider = BridgeProvider(tenant=tenant, output_dir=output_dir, client=client)

        safe = SafeProvider(provider, gate, approved=approved)
        report = await execute_graph(graph, safe, session=session)

        trace: AgentTrace = build_trace(run_id, request, intent, graph, report)
        record_trace(trace)

        return HandleResult(
            ok=report.ok,
            run_id=run_id,
            verdict=trace.verdict,
            deterministic_hash=trace.deterministic_hash,
            trace=trace.as_dict(),
            error=report.error,
        )
    except Exception as exc:  # noqa: BLE001 — the loop must fail with evidence, not crash
        return HandleResult(
            ok=False,
            run_id=run_id,
            verdict="ERROR",
            error=f"{type(exc).__name__}: {exc}",
        )
