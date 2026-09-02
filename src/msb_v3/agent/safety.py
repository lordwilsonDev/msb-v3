"""Agent action safety gate (blueprint Layer 5, inversion A8).

Severity tiers alone are not enough — a "read file" (low tier) whose content
injects instructions can drive a "send message" (high tier) the gate would
happily approve. So the gate keys on TWO axes:

1. action severity: capability -> risk tier (read=1 ... permissions=4)
2. provenance taint: did this action's inputs originate from untrusted
   content (retrieval results, file contents, web)? Tainted writes are
   REVIEW-gated regardless of their low nominal tier.

Verds: SAFE (execute) / REVIEW (human approval) / BLOCK (quarantine) / UNKNOWN (not registered — policy-dependent, never SAFE by default).
Fail-closed: the governance kill switch blocks everything. Every refusal is
written to the UAC audit chain, mirroring governance/guard.py.

The SafeProvider wraps a ToolProvider: it gates every tool call before
delegating, tracks taint per task, and raises GateBlocked / GateReview so the
executor's generic failure path (and the failure classifier's "blocked" ->
unsafe signal) handles refusals without any special casing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from msb_ledger.audit_chain import AuditChainLike
from msb_ledger.chain_anchor import anchored_chain_from_env
from msb_v3.agent.dag import Task
from msb_v3.agent.executor import ToolProvider
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.observability.metrics import ACTIONGATE_DECISIONS

logger = logging.getLogger(__name__)
# capability -> risk tier (blueprint §7 tier table, trimmed to the slice +
# the dangerous actions that must never run unapproved).
RISK_TIERS: Dict[str, int] = {
    "read_vault": 1,
    "llm_synthesis": 1,
    "web_search": 1,
    "write_file": 2,
    "vault_delete": 3,
    "send_message": 3,
    "financial": 4,
    "permissions": 4,
}

# tool name -> slice capability (the SafeProvider maps before gating)
TOOL_CAPABILITY: Dict[str, str] = {
    "search_query": "read_vault",
    "vault_read": "read_vault",
    "chat": "llm_synthesis",
    "vault_write": "write_file",
}

# Sentinel tier for a capability the gate does not recognize.
# UNKNOWN is deliberately not 1 (that's the old default that hid the gap).
_UNKNOWN_TIER = -1

# Tools whose results carry untrusted content (the taint source)
_TAINTED_TOOLS = frozenset({"search_query", "vault_read"})

# Tainted writes always need human approval, whatever their nominal tier.
_TAINT_ESCALATED = frozenset(
    {"write_file", "vault_delete", "send_message", "financial", "permissions"}
)

REVIEW_TIER = 3
BLOCK_TIER = 4


class GateBlocked(Exception):
    def __init__(self, verdict: "GateVerdict") -> None:
        self.verdict = verdict
        super().__init__(f"action blocked: {verdict.reason}")


class GateReview(Exception):
    def __init__(self, verdict: "GateVerdict") -> None:
        self.verdict = verdict
        super().__init__(f"action review required: {verdict.reason}")


@dataclass
class GateVerdict:
    allowed: bool
    action: str  # SAFE | REVIEW | BLOCK | UNKNOWN
    reason: str
    tier: int = 0
    tainted: bool = False
    detail: Dict[str, Any] = field(default_factory=dict)


class ActionGate:
    def __init__(
        self,
        killswitch: Optional[KillSwitch] = None,
        audit_chain: Optional[AuditChainLike] = None,
    ) -> None:
        self._switch = killswitch  # None = not armed (tests inject fakes)
        self._audit = (
            audit_chain
            if audit_chain is not None
            else anchored_chain_from_env()
        )

    # Hardening hook — override in tests / subclasses that want a custom
    # "is this capability registered?" decision without rewriting the gate.
    # Derived gates (CapabilityResolver, ToolManifest) will use this hook so
    # the default UNKNOWN path stays deterministic and fail-closed.
    def is_registered(self, capability: str) -> bool:
        return capability in RISK_TIERS

    def tier_of(self, capability: str) -> int:
        if not self.is_registered(capability):
            return _UNKNOWN_TIER
        return RISK_TIERS[capability]

    def gate(
        self,
        capability: str,
        *,
        tainted_inputs: bool = False,
        approved: Optional[set[str]] = None,
        granted: Optional[set[str]] = None,
        agent_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> GateVerdict:
        """Gate one capability.

        The gate now distinguishes a registered capability (found in
        ``RISK_TIERS``) from an unregistered one (``UNKNOWN``). UNKNOWN is a
        first-class verdict, not a sneaky SAFE — an unknown capability does
        **not** inherit Tier 1 and SAFE by default. The caller must still
        honor the verdict, and the UNKNOWN policy below classifies the
        disposition.

        `approved` is the operator's pre-authorization for this run: a tainted
        write that was declared in the approved plan (capability in approved)
        executes; a tainted write that was NOT declared is REVIEW-gated — the
        A8 correction, enforced without blocking the approved happy path.

        `granted` is the agent's standing capability whitelist (identity §17):
        a capability outside the grant is BLOCKED outright — an agent does
        only what it was registered to do. ``None`` = no whitelist (existing
        callers unchanged).

        `agent_id` / `tenant_id` participate in scoped lockdown (unified-
        architecture §13): ``STOP agent_07`` blocks only agent_07, and
        ``DISABLE <tool capability>`` blocks only that capability — without
        stopping the whole loop. The global arm still blocks everyone.
        """
        try:
            return self._decide(
                capability,
                tainted_inputs=tainted_inputs,
                approved=approved,
                granted=granted,
                agent_id=agent_id,
                tenant_id=tenant_id,
            )
        except Exception:
            # Fail-closed and observable: a gate that raises must never look
            # like an allow. Count it and re-raise — the SafeProvider turns it
            # into a blocked execution, never a silent pass.
            ACTIONGATE_DECISIONS.labels(verdict="failed").inc()
            raise

    def _decide(
        self,
        capability: str,
        *,
        tainted_inputs: bool,
        approved: Optional[set[str]],
        granted: Optional[set[str]],
        agent_id: Optional[str],
        tenant_id: Optional[str],
    ) -> GateVerdict:
        tier = self.tier_of(capability)

        # Unknown-capability guard (hardening Phase 0).
        # UNKNOWN is a first-class verdict. An unmapped capability does NOT
        # inherit Tier 1 / SAFE. The disposition is policy-driven and encoded
        # in one place, not scattered through callers.
        if tier == _UNKNOWN_TIER:
            return self._unknown_disposition(capability, tainted_inputs)

        # Kill switch — cheapest, most absolute, fail-closed. The global arm
        # is checked first (works for every switch, real or fake); scoped
        # blocks are consulted per dimension when the switch supports them
        # (unified-architecture §13) so a scoped arm never bleeds across
        # scopes and never loosens a global lockdown.
        if self._switch is not None:
            if self._switch.is_armed():
                return self._refuse(
                    "BLOCK",
                    "kill switch armed — loop paused",
                    tier,
                    tainted_inputs,
                    capability,
                )
            _is_blocked = getattr(self._switch, "is_blocked", None)
            if _is_blocked is not None:
                if _is_blocked("tool", capability):
                    return self._refuse(
                        "BLOCK",
                        f"kill switch armed for tool scope: {capability}",
                        tier,
                        tainted_inputs,
                        capability,
                    )
                if agent_id is not None and _is_blocked("agent", agent_id):
                    return self._refuse(
                        "BLOCK",
                        f"kill switch armed for agent scope: {agent_id}",
                        tier,
                        tainted_inputs,
                        capability,
                    )
                if tenant_id is not None and _is_blocked("tenant", tenant_id):
                    return self._refuse(
                        "BLOCK",
                        f"kill switch armed for tenant scope: {tenant_id}",
                        tier,
                        tainted_inputs,
                        capability,
                    )

        # Standing capability grant (identity §17): an agent does only what
        # it was registered to do. Fail-closed — missing grant = BLOCK.
        if granted is not None and capability not in granted:
            return self._refuse(
                "BLOCK",
                f"capability not granted to this agent: {capability}",
                tier,
                tainted_inputs,
                capability,
            )

        # A8 correction: tainted writes must not execute on their own.
        if (
            tainted_inputs
            and capability in _TAINT_ESCALATED
            and not (approved and capability in approved)
        ):
            return self._refuse(
                "REVIEW",
                "action driven by untrusted content requires approval",
                tier,
                tainted_inputs,
                capability,
            )

        if tier >= BLOCK_TIER:
            return self._refuse(
                "BLOCK", "action at very-high risk tier", tier, tainted_inputs, capability
            )
        if tier >= REVIEW_TIER:
            return self._refuse(
                "REVIEW", "action at high risk tier", tier, tainted_inputs, capability
            )

        ACTIONGATE_DECISIONS.labels(verdict="allowed").inc()
        return GateVerdict(
            True,
            "SAFE",
            "registered capability, brakes clear",
            tier=tier,
            tainted=tainted_inputs,
        )

    def _refuse(
        self, action: str, reason: str, tier: int, tainted: bool, capability: str
    ) -> GateVerdict:
        verdict = GateVerdict(False, action, reason, tier=tier, tainted=tainted)
        ACTIONGATE_DECISIONS.labels(
            verdict="denied" if action == "BLOCK" else "indeterminate"
        ).inc()
        try:
            self._audit.append(
                "agentic",
                "blocked",
                {"action": action, "reason": reason, "capability": capability},
            )
        except Exception as exc:
            logger.warning("gate audit append failed: %s", exc)
        return verdict

    def _unknown_disposition(
        self, capability: str, tainted_inputs: bool
    ) -> GateVerdict:
        """UNKNOWN-capability policy, encoded centrally.

        Encoded here once, not scattered through callers. The hierarchy is:

        UNKNOWN + consequential capability -> BLOCK
        UNKNOWN + tainted input -> REVIEW (even if the nominal side effect
          looks low-risk; taint is a separate axis the gate tracks)
        UNKNOWN + low-risk read-only -> REVIEW

        The intent is: UNKNOWN never becomes SAFE. For a registered,
        low-risk, untainted, read-style capability the gate can still return
        SAFE today (that path is unchanged and tested). For an UNKNOWN one,
        the system says "I don't know what this is" and defaults toward
        non-execution until policy or a resolver says otherwise.
        """
        if tainted_inputs:
            return self._refuse(
                "REVIEW",
                "unknown capability driven by untrusted content requires approval",
                _UNKNOWN_TIER,
                True,
                capability,
            )
        return self._refuse(
            "BLOCK",
            f"capability not registered: {capability}",
            _UNKNOWN_TIER,
            False,
            capability,
        )

    def _refuse_unknown_read_only(self, capability: str) -> GateVerdict:
        """UNKNOWN + low-risk read-only path, for when a future resolver wants
        to gate a read-only unknown capability as REVIEW instead of BLOCK.

        Kept explicit and separate from the default UNKNOWN disposition so the
        default UNKNOWN path stays BLOCK and the read-only exception is
        intentional and auditable.
        """
        return self._refuse(
            "REVIEW",
            f"registered capability required for this read; unknown capability: {capability}",
            _UNKNOWN_TIER,
            False,
            capability,
        )


class SafeProvider:
    """ToolProvider wrapper that gates every tool call and tracks taint.

    Every call is gated by the ActionGate before delegation, so an unknown
    capability is blocked regardless of what tool name reached the
    provider.
    """

    def __init__(
        self,
        provider: ToolProvider,
        gate: ActionGate,
        *,
        approved: Optional[set[str]] = None,
        granted: Optional[set[str]] = None,
    ) -> None:
        self._provider = provider
        self._gate = gate
        self._approved = set(approved or ())
        self._granted = (
            set(granted) if granted is not None else None
        )  # None = no whitelist
        self._tainted: set[str] = set()  # task_ids whose outputs carry untrusted content

    async def run_tool(
        self, name: str, *, task: Task, inputs: Dict[str, Any], session: str
    ) -> Any:
        capability = TOOL_CAPABILITY.get(
            name, task.required_capabilities[0] if task.required_capabilities else "read_vault"
        )
        declared = task.inputs and [i.get("from") for i in task.inputs] or []
        tainted_inputs = any(
            pid in self._tainted for pid in declared if pid
        )

        verdict = self._gate.gate(
            capability,
            tainted_inputs=tainted_inputs,
            approved=self._approved,
            granted=self._granted,
        )
        if verdict.action == "BLOCK":
            raise GateBlocked(verdict)
        if verdict.action == "REVIEW":
            raise GateReview(verdict)

        result = await self._provider.run_tool(
            name, task=task, inputs=inputs, session=session
        )
        # Taint flows with the data: this task is tainted if it consumed
        # tainted inputs OR produced untrusted content itself — so a write
        # whose brief derives from tainted research stays tainted all the way
        # down the graph (a dead taint at intermediate nodes would let the
        # injected instruction drive the write it was never approved for).
        if tainted_inputs or name in _TAINTED_TOOLS:
            self._tainted.add(task.task_id)
        return result

    def is_task_tainted(self, task_id: str) -> bool:
        return task_id in self._tainted
