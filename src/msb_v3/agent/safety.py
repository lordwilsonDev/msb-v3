"""Agent action safety gate (blueprint Layer 5, inversion A8).

Severity tiers alone are not enough — a "read file" (low tier) whose content
injects instructions can drive a "send message" (high tier) the gate would
happily approve. So the gate keys on TWO axes:

1. action severity: capability -> risk tier (read=1 ... permissions=4)
2. provenance taint: did this action's inputs originate from untrusted
   content (retrieval results, file contents, web)? Tainted writes are
   REVIEW-gated regardless of their low nominal tier.

Verds: SAFE (execute) / REVIEW (human approval) / BLOCK (quarantine).
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

from msb_v3.agent.dag import Task
from msb_v3.agent.executor import ToolProvider
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.uac.audit_chain import AuditChainLike
from msb_v3.uac.chain_anchor import anchored_chain_from_env

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

# Tools whose results carry untrusted content (the taint source)
_TAINTED_TOOLS = frozenset({"search_query", "vault_read"})

# Tainted writes always need human approval, whatever their nominal tier.
_TAINT_ESCALATED = frozenset({"write_file", "vault_delete", "send_message", "financial", "permissions"})

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
    action: str  # SAFE | REVIEW | BLOCK
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
        self._audit = audit_chain if audit_chain is not None else anchored_chain_from_env()

    def tier_of(self, capability: str) -> int:
        return RISK_TIERS.get(capability, 1)

    def gate(
        self,
        capability: str,
        *,
        tainted_inputs: bool = False,
        approved: Optional[set[str]] = None,
    ) -> GateVerdict:
        """Gate one action. Caller must honor the verdict.

        `approved` is the operator's pre-authorization for this run: a tainted
        write that was declared in the approved plan (capability in approved)
        executes; a tainted write that was NOT declared is REVIEW-gated — the
        A8 correction, enforced without blocking the approved happy path.
        """
        tier = self.tier_of(capability)

        # Kill switch — cheapest, most absolute, fail-closed.
        if self._switch is not None and self._switch.is_armed():
            return self._refuse("BLOCK", "kill switch armed — loop paused", tier, tainted_inputs, capability)

        # A8 correction: tainted writes must not execute on their own.
        if tainted_inputs and capability in _TAINT_ESCALATED and not (approved and capability in approved):
            return self._refuse(
                "REVIEW",
                "action driven by untrusted content requires approval",
                tier,
                tainted_inputs,
                capability,
            )

        if tier >= BLOCK_TIER:
            return self._refuse("BLOCK", "action at very-high risk tier", tier, tainted_inputs, capability)
        if tier >= REVIEW_TIER:
            return self._refuse("REVIEW", "action at high risk tier", tier, tainted_inputs, capability)

        return GateVerdict(True, "SAFE", "brakes clear", tier=tier, tainted=tainted_inputs)

    def _refuse(self, action: str, reason: str, tier: int, tainted: bool, capability: str) -> GateVerdict:
        verdict = GateVerdict(False, action, reason, tier=tier, tainted=tainted)
        try:
            self._audit.append("agentic", "blocked", {"action": action, "reason": reason, "capability": capability})
        except Exception as exc:
            logger.warning("gate audit append failed: %s", exc)
        return verdict


class SafeProvider:
    """ToolProvider wrapper that gates every tool call and tracks taint."""

    def __init__(
        self,
        provider: ToolProvider,
        gate: ActionGate,
        *,
        approved: Optional[set[str]] = None,
    ) -> None:
        self._provider = provider
        self._gate = gate
        self._approved = set(approved or ())
        self._tainted: set[str] = set()  # task_ids whose outputs carry untrusted content

    async def run_tool(self, name: str, *, task: Task, inputs: Dict[str, Any], session: str) -> Any:
        capability = TOOL_CAPABILITY.get(name, task.required_capabilities[0] if task.required_capabilities else "read_vault")
        declared = task.inputs and [i.get("from") for i in task.inputs] or []
        tainted_inputs = any(pid in self._tainted for pid in declared if pid)

        verdict = self._gate.gate(capability, tainted_inputs=tainted_inputs, approved=self._approved)
        if verdict.action == "BLOCK":
            raise GateBlocked(verdict)
        if verdict.action == "REVIEW":
            raise GateReview(verdict)

        result = await self._provider.run_tool(name, task=task, inputs=inputs, session=session)
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
