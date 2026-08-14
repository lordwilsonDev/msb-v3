"""Deterministic Sovereign Node policy; model output is never authoritative."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from msb_v3.governance.killswitch import KillSwitch
from msb_v3.node.approval import NodeApprovalStore

DECISIONS = ("ALLOW", "ALLOW_WITH_LIMITS", "REQUIRE_APPROVAL", "DENY", "QUARANTINE")
CAPABILITIES = {
    "SCREEN_READ",
    "FILE_READ",
    "FILE_WRITE",
    "SHELL_EXEC",
    "GUI_CLICK",
    "GUI_TYPE",
    "BROWSER_NAVIGATE",
    "NETWORK_REQUEST",
    "APP_LAUNCH",
}


class PolicyDecision:
    def __init__(self, decision: str, risk_level: str, reasons: list[str], **kwargs: Any) -> None:
        self.decision = decision
        self.risk_level = risk_level
        self.reasons = reasons
        self.data: Dict[str, Any] = kwargs

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "risk_level": self.risk_level,
            "reasons": self.reasons,
            **self.data,
        }


class NodePolicy:
    def __init__(self, root: str | Path, approval_queue: NodeApprovalStore, kill_switch: KillSwitch, grant_ttl_s: int = 120) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.approval_queue = approval_queue
        self.kill_switch = kill_switch
        self.grant_ttl_s = grant_ttl_s

    def evaluate(self, request_id: str, device_id: str, intent: Dict[str, Any]) -> PolicyDecision:
        if self.kill_switch.is_armed():
            return PolicyDecision("QUARANTINE", "L7", ["node kill switch is armed"])
        intent_type = str(intent.get("type", ""))
        target = intent.get("target")
        requested = intent.get("requested_capabilities")
        if not isinstance(target, dict) or not isinstance(requested, list):
            return PolicyDecision("DENY", "L7", ["intent is missing a structured target or capability list"])
        capabilities = {str(value).upper() for value in requested}
        unknown = sorted(capabilities - CAPABILITIES)
        if unknown:
            return PolicyDecision("DENY", "L7", [f"unknown capabilities: {', '.join(unknown)}"])
        if intent_type == "read_file" and capabilities == {"FILE_READ"}:
            path = target.get("path")
            if not isinstance(path, str) or not path:
                return PolicyDecision("DENY", "L7", ["FILE_READ requires a target path"])
            return self._grant(request_id, device_id, "FILE_READ", "L1", {"root": str(self.root), "path": path})
        if "FILE_WRITE" in capabilities:
            approval_id = self.approval_queue.submit(request_id, device_id, intent)
            return PolicyDecision(
                "REQUIRE_APPROVAL",
                "L3",
                ["file mutation requires owner approval"],
                approval_id=approval_id,
            )
        if capabilities & {"SHELL_EXEC", "GUI_CLICK", "GUI_TYPE", "BROWSER_NAVIGATE", "APP_LAUNCH", "NETWORK_REQUEST"}:
            return PolicyDecision("DENY", "L4", ["capability is not enabled in the first vertical slice"])
        return PolicyDecision("DENY", "L7", ["intent type and capabilities do not match an enabled policy"])

    def _grant(self, request_id: str, device_id: str, capability: str, risk: str, scope: Dict[str, Any]) -> PolicyDecision:
        expires = datetime.now(timezone.utc) + timedelta(seconds=self.grant_ttl_s)
        grant = {
            "grant_id": uuid.uuid4().hex,
            "capability": capability,
            "device_id": device_id,
            "request_id": request_id,
            "scope": scope,
            "expires_at": expires.isoformat(),
            "max_operations": 1,
        }
        return PolicyDecision("ALLOW_WITH_LIMITS", risk, ["read-only capability is within the configured sandbox"], grant=grant)
