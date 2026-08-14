"""Approved, reversible FILE_WRITE capability for the Vesta boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.node.filesystem import CapabilityViolation, FileWriter, FileWriteReceipt
from msb_v3.uac.audit_chain import AuditChain
from msb_v3.vesta.approvals import ApprovalError, VestaApprovalStore
from msb_v3.vesta.evidence import EvidenceError, EvidenceStore
from msb_v3.vesta.models import ABind, VestaFileWriteRequest
from msb_v3.vesta.policy import PolicyDecision
from msb_v3.vesta.runtime import TaskLifecycleError, VestaTaskStore


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _repo_path(value: str) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else Path(settings.msb_home) / path)


class VestaWriteService:
    def __init__(
        self,
        audit: AuditChain,
        tasks: VestaTaskStore,
        evidence: EvidenceStore,
        approvals: VestaApprovalStore,
        writer: FileWriter,
        kill_switch: KillSwitch,
    ) -> None:
        self.audit = audit
        self.tasks = tasks
        self.evidence = evidence
        self.approvals = approvals
        self.writer = writer
        self.kill_switch = kill_switch

    def _transition(
        self,
        task_id: str,
        state: str,
        event_ids: List[int],
        *,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        task = self.tasks.transition(task_id, state, reason=reason, metadata=metadata)
        event_ids.append(
            self.audit.append(
                "vesta",
                "task.transition",
                {
                    "task_id": task_id,
                    "from_state": task["transitions"][-1]["from_state"],
                    "to_state": state,
                    "reason": reason,
                    "metadata": metadata or {},
                },
            ).seq
        )
        return task

    def submit(self, body: VestaFileWriteRequest) -> Dict[str, Any]:
        content = body.content.encode("utf-8")
        bind = ABind.create(body.session, ["filesystem.write"], ttl_seconds=300)
        payload_hash = _digest(content)
        request_evidence = self.evidence.record_json(
            {
                "bind_id": bind.bind_id,
                "task_id": bind.task_id,
                "target_path": body.path,
                "payload_sha256": payload_hash,
                "expected_sha256": body.expected_sha256,
            },
            "vesta.file_write_request",
            {"bind_id": bind.bind_id, "task_id": bind.task_id},
        )
        payload_evidence = self.evidence.record_bytes(
            content,
            "vesta.file_write_payload",
            {"bind_id": bind.bind_id, "task_id": bind.task_id, "sha256": payload_hash},
        )
        evidence_refs = [request_evidence["evidence_id"], payload_evidence["evidence_id"]]
        self.tasks.create(
            bind,
            metadata={
                "target_path": body.path,
                "payload_sha256": payload_hash,
                "expected_sha256": body.expected_sha256,
                "evidence_refs": evidence_refs,
            },
        )
        event_ids: List[int] = [
            self.audit.append(
                "vesta",
                "request.received",
                {
                    "bind_id": bind.bind_id,
                    "task_id": bind.task_id,
                    "capability": "filesystem.write",
                    "target_path": body.path,
                    "payload_sha256": payload_hash,
                    "evidence_refs": evidence_refs,
                },
            ).seq
        ]
        self._transition(bind.task_id, "AUTHENTICATED", event_ids, metadata={"evidence_refs": evidence_refs})
        self._transition(bind.task_id, "PLANNED", event_ids, metadata={"planner": "vesta-file-write"})
        decision = PolicyDecision(
            "REQUIRE_APPROVAL",
            "high",
            bind.capabilities,
            ("filesystem mutation requires exact owner approval",),
            bind.policy_version,
        )
        decision_evidence = self.evidence.record_json(
            decision.as_dict(),
            "vesta.file_write_policy",
            {"bind_id": bind.bind_id, "task_id": bind.task_id},
        )
        evidence_refs.append(decision_evidence["evidence_id"])
        self.tasks.update_metadata(bind.task_id, {"evidence_refs": evidence_refs})
        approval = self.approvals.submit(
            bind.task_id,
            bind.bind_id,
            body.path,
            payload_evidence["evidence_id"],
            payload_hash,
            body.expected_sha256,
            bind.policy_version,
            bind.deadline,
        )
        self._transition(
            bind.task_id,
            "WAITING_APPROVAL",
            event_ids,
            reason="owner approval required",
            metadata={"approval_id": approval["approval_id"], "evidence_refs": evidence_refs},
        )
        event_ids.append(
            self.audit.append(
                "vesta",
                "authorization.required",
                {
                    "bind_id": bind.bind_id,
                    "task_id": bind.task_id,
                    "approval_id": approval["approval_id"],
                    "decision": decision.decision,
                    "evidence_refs": evidence_refs,
                },
            ).seq
        )
        return {
            "status": "approval_required",
            "task_id": bind.task_id,
            "bind_id": bind.bind_id,
            "approval_id": approval["approval_id"],
            "target_path": approval["target_path"],
            "payload_sha256": approval["payload_sha256"],
            "expected_sha256": approval["expected_sha256"],
            "expires_at": approval["expires_at"],
            "decision": decision.decision,
            "risk_class": decision.risk_class,
            "policy_version": bind.policy_version,
            "evidence_refs": evidence_refs,
            "audit_event_ids": event_ids,
        }

    def approve_and_execute(self, approval_id: str, operator: str) -> Dict[str, Any]:
        approval = self.approvals.approve(approval_id, operator)
        task_id = str(approval["task_id"])
        task = self.tasks.get(task_id)
        event_ids: List[int] = [
            self.audit.append(
                "vesta",
                "approval.decided",
                {
                    "approval_id": approval_id,
                    "task_id": task_id,
                    "status": "APPROVED",
                    "operator": operator,
                },
            ).seq
        ]
        evidence_refs = list(task["metadata"].get("evidence_refs", []))
        receipt: Optional[FileWriteReceipt] = None
        try:
            self._transition(task_id, "APPROVED", event_ids, metadata={"approval_id": approval_id})
            self._transition(task_id, "EXECUTING", event_ids, metadata={"approval_id": approval_id})
            if self.kill_switch.is_armed():
                self._transition(task_id, "QUARANTINED", event_ids, reason="kill switch armed")
                self._void_approval(approval_id, "kill switch armed", event_ids)
                return {
                    "status": "quarantined",
                    "task_id": task_id,
                    "approval_id": approval_id,
                    "error": "kill switch armed",
                    "audit_event_ids": event_ids,
                    "evidence_refs": evidence_refs,
                }
            content = self.evidence.read_bytes(str(approval["payload_evidence_id"]))
            if _digest(content) != approval["payload_sha256"]:
                raise EvidenceError("approved payload evidence hash changed")
            receipt = self.writer.write(
                str(approval["target_path"]),
                content,
                expected_sha256=approval["expected_sha256"],
            )
            receipt_evidence = self.evidence.record_json(
                receipt.public(),
                "vesta.file_write_receipt",
                {"task_id": task_id, "approval_id": approval_id},
            )
            evidence_refs.append(receipt_evidence["evidence_id"])
            self.tasks.update_metadata(task_id, {"evidence_refs": evidence_refs})
            self._transition(
                task_id,
                "VERIFYING",
                event_ids,
                metadata={"receipt": receipt.public(), "evidence_refs": evidence_refs},
            )
            event_ids.append(
                self.audit.append(
                    "vesta",
                    "execution.verified",
                    {
                        "task_id": task_id,
                        "approval_id": approval_id,
                        "receipt": receipt.public(),
                        "evidence_refs": evidence_refs,
                    },
                ).seq
            )
            self._transition(task_id, "COMPLETED", event_ids, metadata={"evidence_refs": evidence_refs})
            return {
                "status": "completed",
                "task_id": task_id,
                "approval_id": approval_id,
                "receipt": receipt.public(),
                "evidence_refs": evidence_refs,
                "audit_event_ids": event_ids,
            }
        except (ApprovalError, CapabilityViolation, EvidenceError, TaskLifecycleError) as exc:
            rollback: Dict[str, object] = {"ok": True, "performed": False}
            if receipt is not None:
                rollback = {"performed": True, **self.writer.rollback(receipt)}
            try:
                self._transition(
                    task_id,
                    "RECOVERING",
                    event_ids,
                    reason=str(exc),
                    metadata={"rollback": rollback, "evidence_refs": evidence_refs},
                )
                self._transition(
                    task_id,
                    "QUARANTINED",
                    event_ids,
                    reason="write execution failed; operator review required",
                    metadata={"rollback": rollback, "evidence_refs": evidence_refs},
                )
            # Best-effort quarantine recording: ignore TaskLifecycleError
            # when the task is already in a terminal state. The original
            # write failure above stays surfaced; a failed bookkeeping
            # transition should not mask it.
            except TaskLifecycleError:
                pass
            self._void_approval(approval_id, str(exc), event_ids)
            event_ids.append(
                self.audit.append(
                    "vesta",
                    "execution.failed",
                    {
                        "task_id": task_id,
                        "approval_id": approval_id,
                        "error": str(exc),
                        "rollback": rollback,
                        "evidence_refs": evidence_refs,
                    },
                ).seq
            )
            return {
                "status": "quarantined",
                "task_id": task_id,
                "approval_id": approval_id,
                "error": str(exc),
                "rollback": rollback,
                "evidence_refs": evidence_refs,
                "audit_event_ids": event_ids,
            }

    def reject(self, approval_id: str, operator: str, reason: str = "") -> Dict[str, Any]:
        approval = self.approvals.reject(approval_id, operator, reason)
        task_id = str(approval["task_id"])
        event_ids: List[int] = []
        self._transition(task_id, "DENIED", event_ids, reason=reason or "owner rejected write")
        event_ids.append(
            self.audit.append(
                "vesta",
                "approval.rejected",
                {"approval_id": approval_id, "task_id": task_id, "operator": operator, "reason": reason},
            ).seq
        )
        return {"status": "rejected", "approval_id": approval_id, "task_id": task_id, "audit_event_ids": event_ids}

    def _void_approval(self, approval_id: str, reason: str, event_ids: List[int]) -> None:
        """Best-effort VOID of an APPROVED approval whose task quarantined.

        Never masks the original write failure: a bookkeeping failure here is
        swallowed, with the task quarantine as the authoritative record.
        """
        try:
            self.approvals.void(approval_id, reason)
            event_ids.append(
                self.audit.append(
                    "vesta",
                    "approval.voided",
                    {
                        "approval_id": approval_id,
                        "task_id": str(self.approvals.get(approval_id)["task_id"]),
                        "reason": reason,
                    },
                ).seq
            )
        except ApprovalError:
            pass
