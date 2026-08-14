"""Capability Gateway routing — call envelope + decision + the `route()` entry point.

See `docs/blueprints/plans/m1-governance-node-architecture.md` §3
(Compute Plane) and §5 (Experimental Plane). The M1 is the only
authority for "what is a compute call allowed to do"; this module is
that decision, made auditable via `msb_v3.uac.audit_chain.AuditChain`.

Three orthogonal dimensions of the decision:

1. **Capability check.** Does the call hold every required capability
   token granted to the current mission? If not, deny.
2. **Authorization check.** Is `requires_authorization=True`, and a
   human/institutional grant recorded in the context? If yes, deny
   with reason="requires_authorization_not_granted" — the call is
   parked, not refused, so a human signer can lift it without the
   call having to be re-built.
3. **Backend selection.** If allowed, route based on
   `estimated_bytes`: fit-in-local-memory (8 GB on M1; configurable
   via `MSB_LOCAL_MEMORY_BUDGET_BYTES`) goes to the active local
   backend (Ollama/llama.cpp via `local_ai.client_factory`); else
   frontier seam (`core.config.settings.openai_frontier_url`).

Every call — allowed OR denied — appends one record to the audit
chain so the "why was this routed where" answer is replayable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional

from msb_v3.local_ai.client_factory import active_backend
from msb_v3.uac.audit_chain import AuditChain

logger = logging.getLogger(__name__)

# Memory budget the M1 can spare for one inference. 8 GB is the
# lower-bound M1 unified-memory spec; default to a conservative
# 6 GB so OS + python runtime aren't pushed out. Override via
# MSB_LOCAL_MEMORY_BUDGET_BYTES (raw integer bytes).
_DEFAULT_LOCAL_BUDGET_BYTES = 6 * 1024 * 1024 * 1024

# Backend labels — stable strings; auditors and dashboards read these.
BACKEND_LOCAL_OLLAMA = "local.ollama"
BACKEND_LOCAL_LLAMACPP = "local.llamacpp"
BACKEND_FRONTIER = "frontier"


@dataclass(frozen=True)
class GatewayCall:
    """The thing the runtime wants the gateway to evaluate.

    `name` is a stable identifier (`llm.infer`, `tool.shell`,
    `experiment.intervene`); `estimated_bytes` is a *coarse* heuristic
    used for local-vs-remote only — the gateway never inspects the
    payload itself. `capabilities` are required tokens; the call is
    denied if any is missing from the context.

    `requires_authorization=True` maps to the §5 Experimental Plane
    rule: this call must not execute autonomously. Codified, not
    moral — see `GatewayContext.granted_authorizations` for the gate.
    """
    name: str
    estimated_bytes: int = 0
    capabilities: FrozenSet[str] = field(default_factory=frozenset)
    requires_authorization: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayContext:
    """Runtime context the gateway evaluates the call against.

    `granted_capabilities` is the set of tokens the current mission
    holds (typically populated from `MissionAnchor`'s scope_hash +
    `SBOMRegistry`'s entry for this server). `granted_authorizations`
    is the set of authorization tokens — keys like
    `experiment.intervene:slug-<rj-id>` — that humans/institutions
    have signed off on. `local_budget_bytes` lets a test override the
    M1's real 8 GB without poking env vars.
    """
    granted_capabilities: FrozenSet[str] = field(default_factory=frozenset)
    granted_authorizations: FrozenSet[str] = field(default_factory=frozenset)
    local_budget_bytes: int = _DEFAULT_LOCAL_BUDGET_BYTES


@dataclass(frozen=True)
class GatewayDecision:
    """The gateway's verdict.

    `authorized=True` and `backend` non-None: proceed. Caller passes
    every GatewayCall — allowed or denied — through the same code
    path so the audit chain captures the whole stream of attempts,
    not only the successful ones.

    `decision_id` is the audit-chain record hash (sha256 hex); pass
    it to `AuditChain.verify(decision_id)` to confirm the decision
    is in the chain and unaltered.
    """
    authorized: bool
    backend: Optional[str]
    reason: str
    decision_id: str


def route(call: GatewayCall, ctx: Optional[GatewayContext] = None) -> GatewayDecision:
    """Evaluate one GatewayCall and return the GatewayDecision.

    Never raises. Result is always audit-logged — denials land in
    the same chain as grants, so the runtime can answer "why was
    call X refused at 14:32 on Tuesday?" after the fact.
    """
    ctx = ctx or GatewayContext()
    chain = AuditChain()

    # 1. Capability check.
    missing = call.capabilities - ctx.granted_capabilities
    if missing:
        return _log_decision(
            chain,
            call,
            ctx,
            authorized=False,
            backend=None,
            reason=f"missing_capabilities:{','.join(sorted(missing))}",
        )

    # 2. Authorization check (Experimental Plane §5).
    if call.requires_authorization:
        # The caller is asking for a human/institutional grant for
        # this specific call. Match by `name` (e.g. "experiment.intervene"
        # needs a `name`-keyed authorization) until a richer
        # claim/scope model exists.
        needed = f"{call.name}:{call.metadata.get('slug', '*')}"
        if needed not in ctx.granted_authorizations:
            return _log_decision(
                chain,
                call,
                ctx,
                authorized=False,
                backend=None,
                reason=(
                    f"requires_authorization_not_granted (needed={needed!r}; "
                    f"granted={sorted(ctx.granted_authorizations)})"
                ),
            )

    # 3. Backend selection (Compute Plane §3).
    if call.estimated_bytes <= ctx.local_budget_bytes:
        backend = (
            BACKEND_LOCAL_LLAMACPP
            if active_backend() == "llamacpp"
            else BACKEND_LOCAL_OLLAMA
        )
        reason = (
            f"fits_in_local_budget:bytes={call.estimated_bytes}"
            f"<=budget={ctx.local_budget_bytes}"
        )
    else:
        backend = BACKEND_FRONTIER
        reason = (
            f"exceeds_local_budget:bytes={call.estimated_bytes}"
            f">budget={ctx.local_budget_bytes}; routed to frontier seam"
        )

    return _log_decision(
        chain,
        call,
        ctx,
        authorized=True,
        backend=backend,
        reason=reason,
    )


def _log_decision(
    chain: AuditChain,
    call: GatewayCall,
    ctx: GatewayContext,
    *,
    authorized: bool,
    backend: Optional[str],
    reason: str,
) -> GatewayDecision:
    """Append the routing decision to the audit chain and surface it.

    The recorded payload is the *decision* — not the call payload —
    so the chain answers "was the routing decision itself correct"
    without storing user-provided data twice. Decision visibility
    is the safety invariant.
    """
    payload: Dict[str, Any] = {
        "call": call.name,
        "estimated_bytes": call.estimated_bytes,
        "requires_authorization": call.requires_authorization,
        "authorized": authorized,
        "backend": backend,
        "reason": reason,
        "granted_capabilities": sorted(ctx.granted_capabilities),
        "granted_authorizations": sorted(ctx.granted_authorizations),
        "local_budget_bytes": ctx.local_budget_bytes,
    }
    record = chain.append(
        component="gateway",
        event_type=("call.allowed" if authorized else "call.denied"),
        payload=payload,
    )
    if not authorized:
        # Quiet log at INFO (not DEBUG) so denials stay operationally
        # visible without being noisy — denials are events, not debug.
        logger.info(
            "gateway denied call=%s reason=%s decision_id=%s",
            call.name, reason, record.record_hash[:12],
        )
    return GatewayDecision(
        authorized=authorized,
        backend=backend,
        reason=reason,
        decision_id=record.record_hash,
    )
