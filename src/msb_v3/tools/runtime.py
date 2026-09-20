"""Governed tool registration (unified-architecture §5).

The single rule this module enforces: **no tool may be registered directly
with the model unless its execution path terminates inside the governance
perimeter.** Registration therefore:

    1. only accepts tools that exist in the canonical registry (``TOOLS``);
    2. checks the kill switch first — global arm, then the tool/tenant scope
       (fail-closed; added 2026-09-12, a chaos test found this path was the
       only one of the two tool-execution mechanisms in the codebase that
       never consulted it — see ``_killswitch_block_reason``'s docstring);
    3. wraps each one in a capability gate (``required_capabilities`` against
       the request's granted capabilities — fail-closed, default grants
       nothing beyond the registered read tools);
    4. records every execution to the AuditChain (best-effort, never fatal).

This is what fixes the forensic finding that /chat advertised tools with no
registered implementations: ``register_governed_tools`` is called by the
ChatHarness before the tool loop runs, for exactly the tools the caller
advertised.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from msb_v3.governance.identity_shadow import (
    SURFACE_IN_PROCESS,
    shadow_identity_decision,
)
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.tools import executors
from msb_v3.tools.registry import TOOLS

logger = logging.getLogger(__name__)


def _audit_append(
    tool_id: str,
    args: Dict[str, Any],
    result: str,
    *,
    tenant: str,
    session: str,
    verdict: str = "error",
) -> None:
    """Best-effort audit of a governed tool call. The chain is the record of
    what actually happened; an append failure is logged, never fatal (the
    tool result still returns — the caller asked for the tool, not the audit).
    Tool content is excluded from the audit payload (secrets hygiene).

    ``verdict`` is the machine-readable gate outcome (M2 observability):
    "allowed" | "denied" | "approval-required" | "unknown" | "error". The
    audit record therefore answers "under which policy, with what verdict"
    without parsing the prose result."""
    try:
        from msb_ledger.chain_anchor import anchored_chain_from_env

        anchored_chain_from_env().append(
            "tools",
            f"tool.{tool_id}",
            {
                "tenant": tenant,
                "session": session,
                "verdict": verdict,
                "args": {k: v for k, v in args.items() if k != "content"},
                "result_head": str(result)[:200],
            },
        )
    except Exception as exc:
        logger.debug("governed tool audit append failed: %s", exc)


def _killswitch_block_reason(tool_id: str, tenant: str) -> str | None:
    """None = not blocked. Otherwise the reason string.

    Found 2026-09-12 (chaos test): this tool-execution path -- what /chat's
    tool calling actually runs through -- never consulted the killswitch at
    all, global or scoped. agent/handle.py's ActionGate does (the same
    KillSwitch state, checked the same way: global arm first, then the
    "tool" scope), so two independent tool-execution mechanisms enforced
    two different things. Not currently exploitable through /chat (it
    never grants capabilities either, so capability-denial already blocks
    everything first) -- but that's a coincidence of an unrelated
    restriction, not a real protection, and would silently stop protecting
    anything the moment capability-granting is ever added to /chat.
    Fail-closed on any error constructing/reading the switch (matches
    KillSwitch's own state()/scope_state() fail-closed behavior on read
    failure) -- a broken killswitch must never look like "not armed".
    """
    try:
        switch = KillSwitch()
        if switch.is_armed():
            return "kill switch armed (global)"
        if switch.is_blocked("tool", tool_id):
            return f"kill switch armed for tool scope: {tool_id}"
        if switch.is_blocked("tenant", tenant):
            return f"kill switch armed for tenant scope: {tenant}"
    except Exception as exc:
        logger.warning("killswitch check failed for tool %s; failing closed (blocked): %s", tool_id, exc)
        return f"killswitch unreadable, failing closed: {exc}"
    return None


def _run_governed(
    tool_id: str,
    args: Dict[str, Any],
    *,
    granted: frozenset,
    tenant: str,
    session: str,
    approved: frozenset = frozenset(),
    actor_id: str | None = None,
    surface: str = SURFACE_IN_PROCESS,
) -> str:
    """Kill switch + approval gate + capability gate + contained execution + audit.

    Every outcome — allow, deny, approval-required, tool-error, unknown,
    blocked — returns a structured string AND is written to the AuditChain
    (best-effort, never fatal), so a refusal leaves evidence rather than an
    absent result. ``approved`` is the set of tool ids the caller's context
    pre-authorized for approval-required tools (fail-closed: absent =
    refused).

    ``actor_id`` is the acting principal, if any caller supplies one. It has no
    effect on this function's decision today: it is forwarded to the identity
    shadow recorder (governance/identity_shadow.py, Deliverable 02 §10), which
    persists what the kernel's identity guards WOULD have decided and applies
    nothing. ``surface`` names the entry path for those records; a caller that
    names none is recorded as ``in-process`` — a positive statement that the
    call did not arrive through a live surface, not the blank it used to leave.
    """
    td = TOOLS.get(tool_id)
    if td is None:
        outcome = f"[tool-error] unknown tool: {tool_id}"
        _audit_append(tool_id, args, outcome, tenant=tenant, session=session, verdict="unknown")
        return outcome
    # Kill switch — cheapest, most absolute, fail-closed. Checked before
    # approval/capability so an armed switch can never be bypassed by
    # unrelated gate logic (same ordering as agent/safety.py's ActionGate).
    block_reason = _killswitch_block_reason(tool_id, tenant)
    if block_reason is not None:
        outcome = f"[blocked] tool {tool_id}: {block_reason}"
        _audit_append(tool_id, args, outcome, tenant=tenant, session=session, verdict="blocked")
        return outcome
    if td.approval_required and tool_id not in approved:
        outcome = f"[approval-required] tool {tool_id} requires operator approval"
        _audit_append(tool_id, args, outcome, tenant=tenant, session=session, verdict="approval-required")
        return outcome
    missing = [c for c in td.required_capabilities if c not in granted]
    if missing:
        outcome = f"[denied] tool {tool_id} requires capabilities: {', '.join(missing)}"
        _audit_append(tool_id, args, outcome, tenant=tenant, session=session, verdict="denied")
        return outcome
    # Identity shadow — observe, change nothing (Deliverable 02 §10). Records
    # what the identity guards would decide about this actor and discards the
    # verdict. Deliberately placed after the existing gates so the records
    # answer the only question worth asking: of the calls that execute TODAY,
    # how many would an identity requirement stop? It cannot return, raise, or
    # alter the outcome below.
    shadow_identity_decision(
        surface=surface,
        tool_id=tool_id,
        tenant=tenant,
        session=session,
        actor_id=actor_id,
        capability=td.required_capabilities[0] if td.required_capabilities else None,
        required_capabilities=tuple(td.required_capabilities),
        declared_risk_class=td.risk_class,
    )
    # Dotted tool ids (codegraph.explore) map to underscore executors
    # (codegraph_explore) — Python attributes cannot contain dots.
    executor: Callable[..., str] | None = getattr(executors, tool_id.replace(".", "_"), None)
    if executor is None:
        outcome = f"[tool-error] no executor registered for {tool_id}"
        _audit_append(tool_id, args, outcome, tenant=tenant, session=session, verdict="error")
        return outcome
    result = executor(args, tenant=tenant, session=session)
    _audit_append(tool_id, args, result, tenant=tenant, session=session, verdict="allowed")
    return result


def register_governed_tools(client: Any, context: Dict[str, Any]) -> None:
    """Register every advertised tool that the perimeter can back.

    ``context`` keys used: ``tools`` (advertised model schemas), optional
    ``granted_capabilities`` (fail-closed: absent = read-only tools only),
    optional ``approved_tools`` (pre-authorized approval-required tools),
    optional ``tenant`` / ``session`` (audit + retrieval scoping), and optional
    ``actor_id`` / ``surface`` (identity shadow observation only — see
    ``governance/identity_shadow.py``; neither affects any decision here). A
    caller that names no ``surface`` is recorded as ``in-process``. Unknown
    tool names are skipped silently — the model only ever sees schemas the
    perimeter can back.
    """
    register = getattr(client, "register_tool", None)
    if register is None:
        return
    advertised = [
        t.get("name")
        for t in (context.get("tools") or [])
        if isinstance(t, dict) and isinstance(t.get("name"), str)
    ]
    granted = frozenset(context.get("granted_capabilities") or [])
    approved = frozenset(context.get("approved_tools") or [])
    tenant = context.get("tenant", "default")
    session = context.get("session", "default")
    # A blank or non-string declaration is treated as none at all rather than
    # coerced — same fail-closed reading as actor_id below, and for the same
    # reason: a label that reaches the corpus must be one a caller chose.
    # The acting principal, when a caller supplies one. Untrusted input: a
    # non-string or blank value is treated as no actor at all (fail-closed),
    # never coerced into something that could later read as an identity.
    _actor = context.get("actor_id")
    actor_id = _actor if isinstance(_actor, str) and _actor.strip() else None
    _surface = context.get("surface")
    surface = _surface if isinstance(_surface, str) and _surface.strip() else SURFACE_IN_PROCESS
    for tool_id in advertised:
        if tool_id not in TOOLS:
            continue
        # Bind tool_id as a default argument — a plain closure over the loop
        # variable would late-bind and route EVERY tool to the last one
        # advertised (a real bug: registering two tools silently ran the
        # second for both). The model's arguments arrive as **kwargs.
        def _run(_tool_id: str = tool_id, **kwargs: Any) -> str:
            return _run_governed(
                _tool_id,
                kwargs,
                granted=granted,
                tenant=tenant,
                session=session,
                approved=approved,
                actor_id=actor_id,
                surface=surface,
            )

        register(tool_id, _run)
