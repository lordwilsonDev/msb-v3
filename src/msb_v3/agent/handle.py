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

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from msb_v3.agent.bridge_provider import BridgeProvider
from msb_v3.agent.executor import ToolProvider, execute_graph
from msb_v3.agent.intent import Intent, interpret_intent
from msb_v3.agent.planner import plan
from msb_v3.agent.safety import ActionGate, SafeProvider
from msb_v3.agent.trace import AgentTrace, build_trace, record_trace
from msb_v3.evidence.spine import (
    KIND_DECISION,
    KIND_EXECUTION,
    KIND_VERIFICATION,
    DecisionEvidence,
    DecisionEvidenceRecord,
    DecisionEvidenceStore,
)
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.local_ai.llama_client import LlamaCPPClient
from msb_v3.local_ai.ollama import LocalAIClient
from msb_v3.runtime.store import RuntimeStore
from msb_v3.tasks.lifecycle import EventingProvider, TaskLifecycle
from msb_v3.tasks.models import UnifiedTask

logger = logging.getLogger(__name__)

# A delegated worker is high-risk as a capability, but not every task it
# receives is consequential. Only task-language that names an irreversible or
# externally visible action escalates MoIE's CONDITIONAL signals to BLOCK;
# ordinary bounded work still gets the inversion pass without requiring a
# needless approval ceremony.
_HIGH_IMPACT_MARKERS = (
    "production",
    "deploy",
    "release",
    "migration",
    "migrate",
    "delete",
    "remove",
    "financial",
    "payment",
    "publish",
    "send",
    "shell",
    "execute",
    "permission",
)

_AGENT_REGISTRY_IMPORTED = False

# MoIE inversion verdict -> Evidence Spine policy_result. A delegated worker's
# decision vertebra records the gate's verdict: APPROVE becomes ALLOW,
# BLOCKED/ERROR fail closed to DENY, and REVIEW (approval required) stays
# REVIEW.
_INVERSION_RESULT = {
    "APPROVE": "ALLOW",
    "BLOCKED": "DENY",
    "REVIEW": "REVIEW",
    "ERROR": "DENY",
}

# The handle slice has no versioned policy module; the ActionGate tier table is
# the policy. This tag versions the decision records emitted onto the Evidence
# Spine so a future policy change is distinguishable in provenance.
_SPINE_POLICY_VERSION = "handle-gate-v1"


def _spine_append(spine: DecisionEvidenceStore | None, evidence: DecisionEvidence) -> DecisionEvidenceRecord | None:
    """Best-effort Evidence Spine write: the run must never break because the
    spine store is unavailable. Returns the stored record (for linking later
    vertebrae) or None when the spine is absent or the write fails."""
    if spine is None:
        return None
    try:
        return spine.append(evidence)
    except Exception as exc:  # noqa: BLE001 — provenance is best-effort
        logger.warning("spine append failed for %s: %s", evidence.task_id, exc)
        return None


def _resolve_agent(agent_id: str, registry: Any = None) -> Any:
    """Look up an agent identity; returns the identity or raises KeyError."""
    if registry is not None:
        return registry.get(agent_id)
    from msb_v3.agent.identity import AgentRegistry

    return AgentRegistry().get(agent_id)


def _make_observation_sink(lifecycle: TaskLifecycle | None, run_id: str):
    """Best-effort sink wiring worker activity into the task's §27
    observations (OBSERVATION_RECORDED event + appended observation row).
    Returns None when the lifecycle is unavailable — the run never breaks
    because observations cannot be recorded."""
    if lifecycle is None:
        return None

    async def sink(sample: dict) -> None:
        try:
            current = lifecycle.get(run_id)["task"].get("observations", []) or []
            current = list(current) + [sample]
            lifecycle.emit(
                run_id,
                "OBSERVATION_RECORDED",
                {"source": sample.get("source"), "update_count": sample.get("update_count"), "observed_at": sample.get("observed_at")},
                update={"observations": current[-50:]},
            )
            # Live channel: SSE subscribers watch the run as it happens.
            from msb_v3.tasks.observations import publish

            await publish(run_id, sample)
        except Exception as exc:  # noqa: BLE001 — sink is best-effort
            logger.warning("observation sink failed for %s: %s", run_id, exc)

    return sink


async def _delegation_inversion_gate(
    request: str,
    run_id: str,
    agent_identity: Any,
    agent_provider: Any,
    lifecycle: TaskLifecycle | None,
    *,
    tenant: str,
    approve: bool,
    moie: Any = None,
) -> tuple[bool, str, Dict[str, Any]]:
    """Run the §25 MoIE gate before an external worker may start.

    A delegated CLI/Paseo worker is a high-risk execution seam even when its
    requested task sounds harmless. MoIE is therefore a preflight, not a
    model-facing suggestion: BLOCK always denies, CONDITIONAL requires the
    explicit operator approval already carried by ``approve``, and a broken
    inversion service fails closed. The complete decision is persisted in the
    unified task and mirrored to the audit chain through the lifecycle.
    """
    lowered_request = request.lower()
    high_impact = any(marker in lowered_request for marker in _HIGH_IMPACT_MARKERS)
    started = {
        "claim": request,
        "provider": getattr(getattr(agent_provider, "spec", None), "provider_id", "unknown"),
        "high_impact": high_impact,
    }
    _lifecycle_emit(lifecycle, run_id, "INVERSION_STARTED", started)

    try:
        if moie is None:
            from msb_v3.moie import MoIEController

            moie = MoIEController(tenant=tenant)
        decision = await asyncio.to_thread(
            moie.analyze,
            request,
            context={"high_impact": high_impact},
        )
        detail = decision.as_dict()
        verdict = str(detail.get("verdict") or "")
        if verdict not in {"APPROVE", "CONDITIONAL", "BLOCK"}:
            raise ValueError(f"unknown MoIE verdict: {verdict or '<empty>'}")
    except Exception as exc:  # noqa: BLE001 — inversion is a safety gate
        detail = {
            "claim": request,
            "verdict": "BLOCK",
            "blocked": True,
            "error": f"{type(exc).__name__}: {exc}",
        }
        _lifecycle_emit(
            lifecycle,
            run_id,
            "INVERSION_COMPLETED",
            detail,
            update={"inversion": detail, "assumptions": []},
        )
        _lifecycle_emit(
            lifecycle,
            run_id,
            None,
            state="DENIED",
            payload={"reason": "MoIE inversion failed — fail-closed", "error": detail["error"]},
        )
        return False, "ERROR", detail

    assumptions = [
        str(item.get("text"))
        for item in detail.get("assumptions", [])
        if isinstance(item, dict) and item.get("text")
    ]
    _lifecycle_emit(
        lifecycle,
        run_id,
        "INVERSION_COMPLETED",
        {
            "verdict": detail["verdict"],
            "blocked": bool(detail.get("blocked")),
            "confidence": detail.get("confidence"),
            "ids": detail.get("ids", {}),
            "contradictions": len(detail.get("contradictions", [])),
        },
        update={"inversion": detail, "assumptions": assumptions},
    )

    if detail["verdict"] == "BLOCK":
        _lifecycle_emit(
            lifecycle,
            run_id,
            None,
            state="DENIED",
            payload={"reason": "MoIE inversion blocked delegated execution", "inversion": detail},
        )
        return False, "BLOCKED", detail

    if detail["verdict"] == "CONDITIONAL" and not approve:
        _lifecycle_emit(
            lifecycle,
            run_id,
            None,
            state="DENIED",
            payload={"reason": "MoIE requires explicit operator approval", "inversion": detail},
        )
        return False, "REVIEW", detail

    if detail["verdict"] == "CONDITIONAL":
        approval = {
            "kind": "moie-inversion",
            "status": "APPROVED",
            "by": "operator",
            "verdict": detail["verdict"],
            "confidence": detail.get("confidence"),
        }
        _lifecycle_emit(
            lifecycle,
            run_id,
            "CONTRACT_APPROVED",
            approval,
            update={"approvals": [approval]},
        )
    return True, "APPROVE", detail


async def _run_delegated_agent(
    request: str,
    run_id: str,
    agent_identity: Any,
    agent_provider: Any,
    lifecycle: TaskLifecycle | None,
    *,
    session: str,
    tenant: str,
    approve: bool,
    moie: Any = None,
    repo: str | None = None,
    context_engine: Any = None,
    memory_fabric: Any = None,
    spine: DecisionEvidenceStore | None = None,
) -> HandleResult:
    """Delegate the whole task to a worker agent (Claude Code / Codex /
    OpenCode as a bounded subprocess, or a Paseo-managed agent in an
    isolated worktree), with lifecycle + evidence recorded. The worker's
    capabilities are its registered grant — it is a hand, not an authority.

    ``repo`` is the target working copy for Paseo worktree creation; without
    it the provider falls back to the server's cwd (honest but often wrong).

    Delegated runs (CLI worker or Paseo worker) start from a composed
    Context Engine package (spec §4.2.3): the worker's initial prompt is
    L0..L7 curated context + the task, and the composition ledger (layers,
    tokens, G3 reduction) is recorded as a CONTEXT_COMPOSED event + the
    task's context section. Composition is best-effort and injectable — a
    failure degrades to the raw request.

    After the run, the composed context is persisted into the Memory
    Fabric as an architectural memory (spec §4.2.2 — a CONTEXT_COMPOSED
    consumer): future runs can recall what context a task was executed
    under. Also best-effort and injectable — a storage failure degrades
    provenance, never the run.
    """
    import hashlib

    if not agent_provider.available():
        # The task never started — DENIED (not FAILED) is the legal terminal
        # state from CREATED.
        _lifecycle_emit(lifecycle, run_id, None, state="DENIED", payload={"reason": agent_provider.unavailable_reason()})
        return HandleResult(
            ok=False,
            run_id=run_id,
            verdict="ERROR",
            error=f"provider unavailable: {agent_provider.unavailable_reason()}",
        )

    inversion_ok, inversion_verdict, inversion = await _delegation_inversion_gate(
        request,
        run_id,
        agent_identity,
        agent_provider,
        lifecycle,
        tenant=tenant,
        approve=approve,
        moie=moie,
    )

    # Evidence spine (Phase 2.3): the MoIE inversion gate is the governed
    # decision for a delegated worker. The decision vertebra records the
    # verdict even when the delegation is denied, so a refusal leaves a
    # durable decision record rather than an absent execution.
    policy_result = _INVERSION_RESULT.get(inversion_verdict, "DENY")
    risk_level = "elevated" if any(m in request.lower() for m in _HIGH_IMPACT_MARKERS) else "normal"
    decision_record = _spine_append(
        spine,
        DecisionEvidence(
            kind=KIND_DECISION,
            task_id=run_id,
            agent_id=agent_identity.agent_id,
            tenant_id=tenant,
            provider=agent_provider.spec.provider_id,
            model_id=getattr(agent_identity, "model", None),
            policy_version=_SPINE_POLICY_VERSION,
            policy_result=policy_result,
            risk_level=risk_level,
            capability_requested=tuple(agent_identity.granted_capabilities or ()),
            capability_granted=tuple(agent_identity.granted_capabilities or ()) if inversion_ok else (),
            selected_action="delegate",
            available_actions=("delegate",),
        ),
    )
    if not inversion_ok:
        reason = inversion.get("error") or inversion.get("meta_critique") or "MoIE denied delegated execution"
        return HandleResult(
            ok=False,
            run_id=run_id,
            verdict=inversion_verdict,
            error=reason,
            trace={"provider": agent_provider.spec.provider_id, "agent_id": agent_identity.agent_id, "inversion": inversion},
        )

    # Legal transition path: CREATED -> PLANNED -> EXECUTING -> VERIFYING ->
    # COMPLETED/FAILED (the delegation *is* the plan).
    _lifecycle_emit(lifecycle, run_id, None, state="PLANNED", payload={"method": "delegate"})
    _lifecycle_emit(lifecycle, run_id, None, state="EXECUTING")
    if decision_record is not None:
        _spine_append(
            spine,
            DecisionEvidence(
                kind=KIND_EXECUTION,
                parent_decision_id=decision_record.decision_id,
                task_id=run_id,
                agent_id=agent_identity.agent_id,
                tenant_id=tenant,
                provider=agent_provider.spec.provider_id,
                policy_version=_SPINE_POLICY_VERSION,
                policy_result=policy_result,
                risk_level=risk_level,
                capability_granted=tuple(agent_identity.granted_capabilities or ()),
                selected_action="delegate",
            ),
        )
    context: Dict[str, Any] = {
        "tenant": tenant,
        "session": session,
        "approve": approve,
        "inversion": inversion,
    }
    if repo:
        context["repo"] = repo

    # Context Engine (spec §4.2.3, P1): every delegated run (CLI worker or
    # Paseo worker) starts with a composed, budgeted context — L0 system
    # invariants through L7 — so the worker's initial prompt is curated,
    # not a bare task string. The raw request is appended verbatim as a
    # belt-and-suspenders guarantee that the task is present however the
    # package was assembled. Best-effort: a composition failure or empty
    # package degrades to the raw request and never breaks the run.
    worker_goal = request
    package = None
    if agent_provider.spec.kind in ("cli", "paseo"):
        package = None
        try:
            if context_engine is None:
                from msb_v3.fabric.context_engine import ContextEngine

                context_engine = ContextEngine()
            package = context_engine.compose(request, tenant=tenant, session=session, repo=repo)
            if package.text:
                worker_goal = f"{package.text}\n\nTASK: {request}"
        except Exception as exc:  # noqa: BLE001 — composition is best-effort
            logger.warning("context compose failed for %s: %s", run_id, exc)
            package = None
        if package is not None:
            ledger = package.as_dict()
            context["composed_context"] = ledger
            _lifecycle_emit(
                lifecycle, run_id, "CONTEXT_COMPOSED",
                {
                    "total_tokens": ledger["total_tokens"],
                    "reduction_pct": ledger["reduction_pct"],
                    "layers": ledger["layers"],
                },
                update={"context": {**((lifecycle.get(run_id)["task"].get("context") or {}) if lifecycle else {}), "composed": ledger}},
            )

    # Observation sink: worker activity streams into the unified task
    # (OBSERVATION_RECORDED events + the §27 observations section) — the
    # task document becomes a live record of what the worker actually did.
    # Paseo workers stream the daemon's curated activity feed; CLI workers
    # stream their subprocess stdout (both via the same sink contract).
    if agent_provider.spec.kind in ("cli", "paseo"):
        sink = _make_observation_sink(lifecycle, run_id)
        if sink is not None:
            context["observation_sink"] = sink

    result = await agent_provider.execute(
        worker_goal,
        context=context,
        session=session,
    )
    _lifecycle_emit(
        lifecycle, run_id, "AGENT_COMPLETED",
        {"provider": agent_provider.spec.provider_id, "ok": result.ok, "duration_s": result.duration_s},
    )

    # CONTEXT_COMPOSED consumer (spec §4.2.2): the composed context handed
    # to the worker is persisted into the Memory Fabric as an architectural
    # memory once the run is done, so future runs can recall what context a
    # task was executed under. Best-effort — a storage failure logs and
    # degrades provenance, never the run. (`package` is only set on the
    # delegated path — CLI and Paseo workers — so this consumer covers both
    # by construction; local agents never reach this branch.)
    if package is not None:
        memory_id = _persist_composed_context(
            package, run_id, agent_identity, agent_provider,
            tenant=tenant, repo=repo, memory_fabric=memory_fabric,
        )
        if memory_id:
            _lifecycle_emit(
                lifecycle, run_id, "MEMORY_STORED",
                {"memory_id": memory_id, "memory_type": "architectural", "source": "delegation"},
            )
            # Consolidation pass: merge the just-stored architectural memory
            # against existing ones (same project + type + shared tag) and
            # decay by recency. Best-effort — a failure logs and degrades
            # provenance, never the run.
            report = _consolidate_composed_memories(memory_fabric, tenant)
            if report is not None:
                _lifecycle_emit(
                    lifecycle, run_id, "MEMORY_CONSOLIDATED",
                    {
                        "merged": report.get("merged", 0),
                        "deprecations": report.get("deprecations", []),
                        "decayed": report.get("decayed", 0),
                    },
                )
    _lifecycle_emit(lifecycle, run_id, None, state="VERIFYING")
    output_hash = hashlib.sha256((result.output or "").encode()).hexdigest()[:16]
    if result.ok:
        _lifecycle_emit(lifecycle, run_id, "VERIFICATION_PASSED", {"verdict": "PASS", "output_hash": output_hash})
    else:
        _lifecycle_emit(lifecycle, run_id, "VERIFICATION_FAILED", {"verdict": "FAIL", "error": result.error})
    _lifecycle_emit(
        lifecycle, run_id, "EVIDENCE_RECORDED",
        {"output_hash": output_hash, "artifacts": result.artifacts},
        update={
            "verification": {"verdict": "PASS" if result.ok else "FAIL", "method": "provider-output"},
            "evidence": [output_hash],
            "outcome": {"ok": result.ok, "verdict": "PASS" if result.ok else "FAIL", "output_hash": output_hash},
        },
    )
    _lifecycle_emit(lifecycle, run_id, None, state="COMPLETED" if result.ok else "FAILED")
    if decision_record is not None:
        _spine_append(
            spine,
            DecisionEvidence(
                kind=KIND_VERIFICATION,
                parent_decision_id=decision_record.decision_id,
                task_id=run_id,
                agent_id=agent_identity.agent_id,
                tenant_id=tenant,
                provider=agent_provider.spec.provider_id,
                policy_version=_SPINE_POLICY_VERSION,
                policy_result=policy_result,
                risk_level=risk_level,
                selected_action="delegate",
                verification_id=output_hash,
            ),
        )
    return HandleResult(
        ok=result.ok,
        run_id=run_id,
        verdict="PASS" if result.ok else "FAIL",
        error=result.error,
        trace={
            "provider": agent_provider.spec.provider_id,
            "agent_id": agent_identity.agent_id,
            "repo": repo,
            "inversion": inversion,
            **result.as_dict(),
        },
    )


def _persist_composed_context(
    package: Any,
    run_id: str,
    agent_identity: Any,
    agent_provider: Any,
    *,
    tenant: str,
    repo: str | None,
    memory_fabric: Any = None,
) -> Optional[str]:
    """Best-effort CONTEXT_COMPOSED consumer (spec §4.2.2): the composed
    context handed to a delegated worker (CLI or Paseo) is persisted as an
    architectural memory with full provenance (source_agent, task_id,
    tenant, project). Returns the memory_id on success; None on failure —
    a storage failure degrades provenance, never the run."""
    try:
        from msb_v3.memory_fabric.models import MemoryType

        if memory_fabric is None:
            from msb_v3.core.config import settings
            from msb_v3.memory_fabric.fabric import MemoryFabric
            from msb_v3.memory_fabric.store import MemoryFabricStore

            memory_fabric = MemoryFabric(MemoryFabricStore(settings.memory_fabric_db_path))
        item = memory_fabric.store_memory(
            content=package.text,
            type_=MemoryType.ARCHITECTURAL,
            tags=["context-composed", agent_provider.spec.provider_id, "delegation"],
            importance=0.7,
            source_agent=agent_identity.agent_id,
            source="delegation",
            task_id=run_id,
            tenant=tenant,
            project=repo or "",
        )
        return item.memory_id
    except Exception as exc:  # noqa: BLE001 — best-effort consumer
        logger.warning("composed-context memory persist failed for %s: %s", run_id, exc)
        return None


def _consolidate_composed_memories(memory_fabric: Any, tenant: str) -> Optional[Dict[str, Any]]:
    """Best-effort consolidation pass after a delegated run (CLI or Paseo,
    spec §4.2.2): merge near-duplicate architectural memories — including
    the one just stored — and decay every active memory by recency.
    Returns the honest report ({merged, deprecations, decayed, kept}) or
    None on failure; never breaks the run."""
    try:
        if memory_fabric is None:
            from msb_v3.core.config import settings
            from msb_v3.memory_fabric.fabric import MemoryFabric
            from msb_v3.memory_fabric.store import MemoryFabricStore

            memory_fabric = MemoryFabric(MemoryFabricStore(settings.memory_fabric_db_path))
        return memory_fabric.consolidate(tenant, by="delegation")
    except Exception as exc:  # noqa: BLE001 — best-effort consumer
        logger.warning("composed-context consolidation failed for tenant %s: %s", tenant, exc)
        return None


@dataclass
class HandleResult:
    ok: bool
    run_id: str
    verdict: str  # PASS | FAIL | ERROR | REVIEW | BLOCKED
    deterministic_hash: str = ""
    trace: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


def _run_id(request: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"dbb-{stamp}-{abs(hash(request)) % 100000:05d}"


def _lifecycle_emit(lifecycle: TaskLifecycle | None, task_id: str, event_type: str | None, payload: dict | None = None, *, state: str | None = None, update: dict | None = None) -> None:
    """Best-effort lifecycle write: the run must never break because the
    projection store or chain mirror is unavailable (chain remains the
    authoritative record when it is reachable).

    ``state`` without an explicit event means a state-machine transition
    (emits the canonical §28 event: PLAN_CREATED, AGENT_STARTED, ...).
    """
    if lifecycle is None:
        return
    try:
        if event_type is not None:
            lifecycle.emit(task_id, event_type, payload, state=state)
        elif state is not None:
            lifecycle.transition(task_id, state, payload=payload)
        if update:
            lifecycle.update(task_id, update)
    except Exception as exc:
        logger.warning("lifecycle write (%s/%s) failed for %s: %s", event_type, state, task_id, exc)


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
    privacy: bool | None = None,
    lifecycle: TaskLifecycle | None = None,
    agent_id: str | None = None,
    registry: Any = None,
    providers: Any = None,
    repo: str | None = None,
    context_engine: Any = None,
    memory_fabric: Any = None,
    moie: Any = None,
    spine: DecisionEvidenceStore | None = None,
) -> HandleResult:
    """Run the slice end-to-end. Returns the HandleResult (ok + evidence).

    Phase 2 (unified-architecture §27-28): every run becomes a unified task
    with an event-sourced lifecycle — TASK_CREATED, INTENT_INTERPRETED,
    PLAN_CREATED, tool events (TOOL_REQUESTED/TOOL_EXECUTED/MUTATION_COMMITTED),
    VERIFICATION_*, EVIDENCE_RECORDED, TASK_COMPLETED/FAILED — mirrored to the
    AuditChain. All lifecycle writes are best-effort: a store or chain outage
    degrades provenance, never the run itself.

    `agent_id` (unified-architecture §17): when set, the run executes under a
    registered agent identity. A CLI-provider agent (Claude Code / Codex /
    OpenCode) gets the whole task delegated to it as a bounded worker; a
    local agent runs the DAG path with its granted capabilities enforced as
    a fail-closed whitelist at the action gate.
    """
    request = (request or "").strip()
    if not request:
        return HandleResult(ok=False, run_id="", verdict="ERROR", error="empty request")

    run_id = _run_id(request)

    # Agent identity resolution — fail-closed: an unknown or revoked agent
    # never runs.
    agent_identity = None
    agent_provider = None
    if agent_id:
        try:
            agent_identity = _resolve_agent(agent_id, registry)
        except KeyError:
            return HandleResult(ok=False, run_id=run_id, verdict="ERROR", error=f"unknown agent: {agent_id}")
        except Exception as exc:
            return HandleResult(ok=False, run_id=run_id, verdict="ERROR", error=f"agent lookup failed: {exc}")
        if agent_identity.revoked:
            return HandleResult(ok=False, run_id=run_id, verdict="ERROR", error=f"agent revoked: {agent_id}")
        if providers is None:
            from msb_v3.agent.providers import ProviderRegistry

            providers = ProviderRegistry()
        agent_provider = providers.get(agent_identity.provider_id)
        if agent_provider is None:
            return HandleResult(ok=False, run_id=run_id, verdict="ERROR", error=f"no provider for agent: {agent_identity.provider_id}")

    # Unified task document (best-effort; a failure here means no lifecycle
    # events, not a broken run).
    task: UnifiedTask | None = None
    if lifecycle is None:
        try:
            lifecycle = TaskLifecycle()
        except Exception as exc:
            logger.warning("task lifecycle unavailable: %s", exc)
            lifecycle = None
    if lifecycle is not None:
        try:
            agent_section = []
            if agent_identity is not None:
                agent_section = [
                    {
                        "agent_id": agent_identity.agent_id,
                        "provider_id": agent_identity.provider_id,
                        "kind": agent_identity.kind,
                        "model": agent_identity.model,
                        "autonomy_level": agent_identity.autonomy_level,
                    }
                ]
            task = UnifiedTask(
                task_id=run_id,
                kind=f"agent.{agent_identity.kind}" if agent_identity else "agent.run",
                tenant=tenant,
                session=session,
                source="api",
                intent={"request": request},
                context={"approve": approve, "privacy_override": privacy is not None},
                agents=agent_section,
                capabilities={
                    "granted": list(agent_identity.granted_capabilities) if agent_identity else [],
                    "required": [],
                },
            )
            lifecycle.create(task)
        except Exception as exc:
            logger.warning("unified task create failed: %s", exc)
            task = None

    # Worker delegation: the whole task goes to an external worker — a CLI
    # agent (Claude Code / Codex / OpenCode) as a bounded subprocess, or a
    # Paseo-managed agent in an isolated worktree. MSB governs, the worker
    # executes; every permission request parks on an operator decision.
    if agent_identity is not None and agent_provider is not None and agent_provider.spec.kind in ("cli", "paseo"):
        return await _run_delegated_agent(
            request, run_id, agent_identity, agent_provider, lifecycle,
            session=session, tenant=tenant, approve=approve, moie=moie, repo=repo,
            context_engine=context_engine, memory_fabric=memory_fabric, spine=spine,
        )

    try:
        # interpret_intent and plan both generate via the client; offload the
        # sync local-model call to a worker thread so the server's event loop
        # stays responsive while a request is in flight (/agent/handle).
        intent: Intent = await asyncio.to_thread(interpret_intent, request, client=client)
        # Explicit privacy override (Phase 2 live test): the caller may force
        # the intent's privacy flag, which drives the router's privacy floor.
        # privacy=None (the default) lets the interpreted intent decide; the
        # model's word is final otherwise.
        if privacy is not None:
            from dataclasses import replace

            intent = replace(intent, privacy=privacy)
        _lifecycle_emit(
            lifecycle, run_id, "INTENT_INTERPRETED",
            {"summary": intent.as_dict()},
            update={"intent": intent.as_dict(), "capabilities": {"granted": sorted(set(intent.permissions)) if approve else []}},
        )
        graph = await plan(intent, client=client)
        _lifecycle_emit(
            lifecycle, run_id, None,  # event comes from the transition below
            state="PLANNED",
            update={"plan": {"goal": graph.goal, "source": graph.source, "tasks": [t.as_dict() for t in graph.tasks]}},
        )

        # Approved capabilities: the operator pre-authorizes the declared
        # permissions (default: none — tainted writes then require review).
        approved = set(intent.permissions) if approve else set()

        # Evidence spine (Phase 2.2): the plan-approval decision is the anchor
        # vertebra for this run's causal chain. Best-effort — a spine outage
        # degrades provenance, never the run.
        decision_record = _spine_append(
            spine,
            DecisionEvidence(
                kind=KIND_DECISION,
                task_id=run_id,
                agent_id=agent_identity.agent_id if agent_identity is not None else None,
                tenant_id=tenant,
                policy_version=_SPINE_POLICY_VERSION,
                policy_result="ALLOW" if approve else "REVIEW",
                risk_level="normal",
                capability_requested=tuple(intent.permissions),
                capability_granted=tuple(intent.permissions) if approve else (),
                selected_action="handle",
                available_actions=tuple(intent.permissions),
            ),
        )

        if gate is None:
            gate = ActionGate(killswitch=KillSwitch())
        if provider is None:
            provider = BridgeProvider(tenant=tenant, output_dir=output_dir, client=client)

        # Capability-scoped permissions (§17): an agent does only what its
        # identity was granted. None = no whitelist (legacy behavior).
        granted: set[str] | None = set(agent_identity.granted_capabilities) if agent_identity is not None else None
        safe: ToolProvider = SafeProvider(provider, gate, approved=approved, granted=granted)
        if lifecycle is not None:
            # Tool-level events flow into the lifecycle (TOOL_REQUESTED /
            # TOOL_EXECUTED / MUTATION_COMMITTED / POLICY_CHECKED).
            safe = EventingProvider(safe, lifecycle, run_id)
        _lifecycle_emit(lifecycle, run_id, None, state="EXECUTING")
        if decision_record is not None:
            _spine_append(
                spine,
                DecisionEvidence(
                    kind=KIND_EXECUTION,
                    parent_decision_id=decision_record.decision_id,
                    task_id=run_id,
                    agent_id=agent_identity.agent_id if agent_identity is not None else None,
                    tenant_id=tenant,
                    policy_version=_SPINE_POLICY_VERSION,
                    policy_result="ALLOW" if approve else "REVIEW",
                    risk_level="normal",
                    capability_granted=tuple(approved),
                    selected_action="handle",
                ),
            )
        store = RuntimeStore()
        report = await execute_graph(graph, safe, session=session, store=store, run_id=run_id)

        trace: AgentTrace = build_trace(run_id, request, intent, graph, report)
        record_trace(trace, store=store)

        _lifecycle_emit(lifecycle, run_id, None, state="VERIFYING")
        if report.ok:
            _lifecycle_emit(lifecycle, run_id, "VERIFICATION_PASSED", {"verdict": trace.verdict, "deterministic_hash": trace.deterministic_hash})
        else:
            _lifecycle_emit(lifecycle, run_id, "VERIFICATION_FAILED", {"verdict": trace.verdict, "error": report.error})
        _lifecycle_emit(
            lifecycle, run_id, "EVIDENCE_RECORDED",
            {"deterministic_hash": trace.deterministic_hash, "run_id": run_id},
            update={
                "verification": {"verdict": trace.verdict, "method": "grounded-registry"},
                "evidence": [trace.deterministic_hash] if trace.deterministic_hash else [],
                "outcome": {"ok": report.ok, "verdict": trace.verdict, "deterministic_hash": trace.deterministic_hash},
                "recovery": {"attempts": 0, "quarantined": False},
            },
        )
        _lifecycle_emit(lifecycle, run_id, None, state="COMPLETED" if report.ok else "FAILED")
        if decision_record is not None:
            _spine_append(
                spine,
                DecisionEvidence(
                    kind=KIND_VERIFICATION,
                    parent_decision_id=decision_record.decision_id,
                    task_id=run_id,
                    agent_id=agent_identity.agent_id if agent_identity is not None else None,
                    tenant_id=tenant,
                    policy_version=_SPINE_POLICY_VERSION,
                    policy_result="ALLOW" if approve else "REVIEW",
                    risk_level="normal",
                    selected_action="handle",
                    verification_id=trace.deterministic_hash or None,
                ),
            )

        return HandleResult(
            ok=report.ok,
            run_id=run_id,
            verdict=trace.verdict,
            deterministic_hash=trace.deterministic_hash,
            trace=trace.as_dict(),
            error=report.error,
        )
    except Exception as exc:  # noqa: BLE001 — the loop must fail with evidence, not crash
        _lifecycle_emit(lifecycle, run_id, None, state="FAILED")
        return HandleResult(
            ok=False,
            run_id=run_id,
            verdict="ERROR",
            error=f"{type(exc).__name__}: {exc}",
        )
