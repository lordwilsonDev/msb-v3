"""Sovereign Node service orchestration."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Dict

from msb_v3.core.config import settings
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.node.approval import NodeApprovalStore
from msb_v3.node.filesystem import CapabilityViolation, FileReader
from msb_v3.node.identity import IdentityStore
from msb_v3.node.policy import NodePolicy
from msb_v3.uac.audit_chain import AuditChain


class NodeService:
    def __init__(
        self,
        identity: IdentityStore,
        policy: NodePolicy,
        reader: FileReader,
        audit: AuditChain,
        kill_switch: KillSwitch,
    ) -> None:
        self.identity = identity
        self.policy = policy
        self.reader = reader
        self.audit = audit
        self.kill_switch = kill_switch

    def enroll(self, device_id: str, public_key: str, pairing_code: str, hardware_assurance: str) -> Dict[str, str]:
        result = self.identity.enroll(device_id, public_key, pairing_code, hardware_assurance)
        self.audit.append("node", "device.enrolled", {"device_id": device_id, "hardware_assurance": hardware_assurance})
        return result

    def challenge(self, device_id: str) -> Dict[str, str]:
        value = self.identity.challenge(device_id)
        return {"device_id": device_id, "challenge": value}

    def open_session(self, device_id: str, challenge: str, signature: str) -> Dict[str, str]:
        result = self.identity.open_session(device_id, challenge, signature)
        self.audit.append("node", "session.opened", {"device_id": device_id, "session_id": result["session_id"]})
        return result

    def compile_intent(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a small text command without granting it any authority."""
        if "command" in intent and "type" not in intent:
            command = str(intent.get("command", "")).strip()
            match = re.fullmatch(r"read(?: file)?\s+(.+)", command, flags=re.IGNORECASE)
            if match:
                return {
                    "type": "read_file",
                    "objective": f"Read {match.group(1)}",
                    "target": {"path": match.group(1)},
                    "requested_capabilities": ["FILE_READ"],
                }
        return {
            "type": intent.get("type"),
            "objective": intent.get("objective", ""),
            "target": intent.get("target"),
            "requested_capabilities": intent.get("requested_capabilities"),
        }

    def engage(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        request_id = str(envelope.get("request_id", ""))
        device_id = self.identity.verify_request(envelope)
        audit_ids = [
            self.audit.append("node", "request.authenticated", {"request_id": request_id, "device_id": device_id}).seq
        ]
        intent = self.compile_intent(envelope["intent"])
        self.audit.append("node", "intent.compiled", {"request_id": request_id, "intent": intent})
        decision = self.policy.evaluate(request_id, device_id, intent)
        policy_data = decision.as_dict()
        audit_ids.append(self.audit.append("node", "policy.decided", {"request_id": request_id, **policy_data}).seq)
        base = {
            "request_id": request_id,
            "decision": decision.decision,
            "risk_level": decision.risk_level,
            "audit_event_ids": audit_ids,
        }
        if decision.decision == "REQUIRE_APPROVAL":
            return {**base, "status": "approval_required", "approval_id": decision.data["approval_id"]}
        if decision.decision in {"DENY", "QUARANTINE"}:
            return {**base, "status": "denied", "error": "; ".join(decision.reasons)}
        grant = decision.data["grant"]
        if self.kill_switch.is_armed():
            return {**base, "status": "denied", "error": "kill switch armed"}
        execution_id = uuid.uuid4().hex
        audit_ids.append(self.audit.append("node", "execution.started", {"execution_id": execution_id, "grant_id": grant["grant_id"]}).seq)
        try:
            if grant["capability"] != "FILE_READ" or grant["max_operations"] < 1:
                raise CapabilityViolation("capability grant is invalid")
            result = self.reader.read(str(intent["target"]["path"]))
            size = result.get("size")
            verification = {
                "ok": bool(result.get("sha256")) and isinstance(size, int) and size >= 0,
                "method": "file_exists_and_sha256",
                "sha256": result.get("sha256"),
            }
            if not verification["ok"]:
                raise CapabilityViolation("file verification failed")
            audit_ids.append(self.audit.append("node", "execution.verified", {"execution_id": execution_id, **verification}).seq)
            return {
                **base,
                "status": "completed",
                "execution_id": execution_id,
                "result": result,
                "verification": verification,
                "audit_event_ids": audit_ids,
            }
        except CapabilityViolation as exc:
            audit_ids.append(self.audit.append("node", "execution.failed", {"execution_id": execution_id, "error": str(exc)}).seq)
            return {**base, "status": "failed", "execution_id": execution_id, "error": str(exc), "audit_event_ids": audit_ids}

    def status(self) -> Dict[str, Any]:
        return {
            "node": "sovereign-node",
            "status": "QUARANTINED" if self.kill_switch.is_armed() else "ACTIVE",
            "sandbox_root": str(self.reader.root),
            "identity": self.identity.status(),
            "protocol": "node.v1",
        }


def _repo_path(value: str) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else Path(settings.msb_home) / path)


def build_service() -> NodeService:
    audit = AuditChain(_repo_path(settings.node_audit_db_path))
    kill_switch = KillSwitch(_repo_path(settings.node_db_path), audit_chain=audit)
    approvals = NodeApprovalStore(_repo_path(settings.node_db_path), audit)
    identity = IdentityStore(
        _repo_path(settings.node_db_path),
        settings.node_pairing_code,
        session_ttl_s=settings.node_session_ttl_s,
        clock_skew_s=settings.node_clock_skew_s,
    )
    reader = FileReader(_repo_path(settings.node_sandbox_root), settings.node_max_read_bytes)
    policy = NodePolicy(reader.root, approvals, kill_switch)
    return NodeService(identity, policy, reader, audit, kill_switch)
