"""Governed tool registration (unified-architecture §5).

The single rule this module enforces: **no tool may be registered directly
with the model unless its execution path terminates inside the governance
perimeter.** Registration therefore:

    1. only accepts tools that exist in the canonical registry (``TOOLS``);
    2. wraps each one in a capability gate (``required_capabilities`` against
       the request's granted capabilities — fail-closed, default grants
       nothing beyond the registered read tools);
    3. records every execution to the AuditChain (best-effort, never fatal).

This is what fixes the forensic finding that /chat advertised tools with no
registered implementations: ``register_governed_tools`` is called by the
ChatHarness before the tool loop runs, for exactly the tools the caller
advertised.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

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
        from msb_v3.uac.chain_anchor import anchored_chain_from_env

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


def _run_governed(
    tool_id: str,
    args: Dict[str, Any],
    *,
    granted: frozenset,
    tenant: str,
    session: str,
    approved: frozenset = frozenset(),
) -> str:
    """Approval gate + capability gate + contained execution + audit.

    Every outcome — allow, deny, approval-required, tool-error, unknown —
    returns a structured string AND is written to the AuditChain (best-effort,
    never fatal), so a refusal leaves evidence rather than an absent result.
    ``approved`` is the set of tool ids the caller's context pre-authorized
    for approval-required tools (fail-closed: absent = refused).
    """
    td = TOOLS.get(tool_id)
    if td is None:
        outcome = f"[tool-error] unknown tool: {tool_id}"
        _audit_append(tool_id, args, outcome, tenant=tenant, session=session, verdict="unknown")
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
    optional ``tenant`` / ``session`` (audit + retrieval scoping). Unknown
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
            )

        register(tool_id, _run)
