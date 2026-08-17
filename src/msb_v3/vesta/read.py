"""Evidence-backed, read-only filesystem capability for the Vesta boundary."""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Dict, List

from msb_ledger.audit_chain import AuditChainLike
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.node.filesystem import CapabilityViolation, FileReader
from msb_v3.vesta.evidence import EvidenceError, EvidenceStore
from msb_v3.vesta.models import ABind, VestaFileReadRequest
from msb_v3.vesta.policy import authorize_file_read
from msb_v3.vesta.runtime import TaskLifecycleError, VestaTaskStore


class VestaReadService:
    """Execute FILE_READ only inside the configured sandbox and record proof."""

    def __init__(
        self,
        audit: AuditChainLike,
        tasks: VestaTaskStore,
        evidence: EvidenceStore,
        reader: FileReader,
        kill_switch: KillSwitch,
    ) -> None:
        self.audit = audit
        self.tasks = tasks
        self.evidence = evidence
        self.reader = reader
        self.kill_switch = kill_switch

    def _transition(
        self,
        task_id: str,
        state: str,
        event_ids: List[int],
        *,
        reason: str = "",
        metadata: Dict[str, Any] | None = None,
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

    @staticmethod
    def _content_bytes(result: Dict[str, object]) -> bytes:
        if result.get("encoding") == "base64":
            value = result.get("content")
            if not isinstance(value, str):
                raise EvidenceError("file reader returned invalid base64 content")
            try:
                return base64.b64decode(value, validate=True)
            except ValueError as exc:
                raise EvidenceError("file reader returned malformed base64 content") from exc
        content = result.get("content")
        if not isinstance(content, str):
            raise EvidenceError("file reader returned invalid text content")
        return content.encode("utf-8")

    def execute(self, body: VestaFileReadRequest, *, actor: str = "operator") -> Dict[str, Any]:
        bind = ABind.create(body.session, ["filesystem.read"], actor=actor)
        request_evidence = self.evidence.record_json(
            {
                "bind_id": bind.bind_id,
                "task_id": bind.task_id,
                "target_path": body.path,
                "capability": "filesystem.read",
            },
            "vesta.file_read_request",
            {"bind_id": bind.bind_id, "task_id": bind.task_id},
        )
        evidence_refs = [request_evidence["evidence_id"]]
        self.tasks.create(
            bind,
            metadata={"target_path": body.path, "evidence_refs": evidence_refs},
        )
        event_ids: List[int] = [
            self.audit.append(
                "vesta",
                "request.received",
                {
                    "bind_id": bind.bind_id,
                    "task_id": bind.task_id,
                    "actor": actor,
                    "capability": "filesystem.read",
                    "target_path": body.path,
                    "evidence_refs": evidence_refs,
                },
            ).seq
        ]
        self._transition(bind.task_id, "AUTHENTICATED", event_ids, metadata={"evidence_refs": evidence_refs})
        self._transition(bind.task_id, "PLANNED", event_ids, metadata={"planner": "vesta-file-read"})
        decision = authorize_file_read(bind)
        policy_evidence = self.evidence.record_json(
            decision.as_dict(),
            "vesta.file_read_policy",
            {"bind_id": bind.bind_id, "task_id": bind.task_id},
        )
        evidence_refs.append(policy_evidence["evidence_id"])
        self.tasks.update_metadata(bind.task_id, {"evidence_refs": evidence_refs})
        event_ids.append(
            self.audit.append(
                "vesta",
                "authorization.decided",
                {
                    "bind_id": bind.bind_id,
                    "task_id": bind.task_id,
                    "evidence_refs": evidence_refs,
                    **decision.as_dict(),
                },
            ).seq
        )
        if decision.decision != "ALLOW":
            self._transition(
                bind.task_id,
                "DENIED",
                event_ids,
                reason="; ".join(decision.reasons),
                metadata={"evidence_refs": evidence_refs},
            )
            return {
                "status": "denied",
                "bind_id": bind.bind_id,
                "task_id": bind.task_id,
                "decision": decision.decision,
                "policy_version": decision.policy_version,
                "evidence_refs": evidence_refs,
                "audit_event_ids": event_ids,
                "error": "; ".join(decision.reasons),
            }

        self._transition(bind.task_id, "AUTHORIZED", event_ids, metadata={"evidence_refs": evidence_refs})
        self._transition(bind.task_id, "EXECUTING", event_ids, metadata={"evidence_refs": evidence_refs})
        if self.kill_switch.is_armed():
            self._transition(
                bind.task_id,
                "QUARANTINED",
                event_ids,
                reason="kill switch armed before read",
                metadata={"evidence_refs": evidence_refs},
            )
            return {
                "status": "quarantined",
                "bind_id": bind.bind_id,
                "task_id": bind.task_id,
                "decision": decision.decision,
                "policy_version": decision.policy_version,
                "evidence_refs": evidence_refs,
                "audit_event_ids": event_ids,
                "error": "kill switch armed",
            }

        try:
            result = self.reader.read(body.path)
            raw_content = self._content_bytes(result)
            if result.get("sha256") != hashlib.sha256(raw_content).hexdigest():
                raise EvidenceError("file read hash does not match returned content")
            content_evidence = self.evidence.record_bytes(
                raw_content,
                "vesta.file_read_content",
                {
                    "bind_id": bind.bind_id,
                    "task_id": bind.task_id,
                    "target_path": result["path"],
                    "sha256": result["sha256"],
                },
            )
            evidence_refs.append(content_evidence["evidence_id"])
            receipt_evidence = self.evidence.record_json(
                {
                    "path": result["path"],
                    "size": result["size"],
                    "sha256": result["sha256"],
                    "encoding": result["encoding"],
                },
                "vesta.file_read_receipt",
                {"bind_id": bind.bind_id, "task_id": bind.task_id},
            )
            evidence_refs.append(receipt_evidence["evidence_id"])
            verification = {
                "ok": True,
                "method": "file_exists_size_and_sha256",
                "sha256": result["sha256"],
            }
            self.tasks.update_metadata(bind.task_id, {"evidence_refs": evidence_refs})
            self._transition(
                bind.task_id,
                "VERIFYING",
                event_ids,
                metadata={"verification": verification, "evidence_refs": evidence_refs},
            )
            event_ids.append(
                self.audit.append(
                    "vesta",
                    "execution.verified",
                    {
                        "bind_id": bind.bind_id,
                        "task_id": bind.task_id,
                        "verification": verification,
                        "evidence_refs": evidence_refs,
                    },
                ).seq
            )
            self._transition(bind.task_id, "COMPLETED", event_ids, metadata={"evidence_refs": evidence_refs})
            return {
                "status": "completed",
                "bind_id": bind.bind_id,
                "task_id": bind.task_id,
                "decision": decision.decision,
                "policy_version": decision.policy_version,
                "result": result,
                "verification": verification,
                "evidence_refs": evidence_refs,
                "audit_event_ids": event_ids,
            }
        except (CapabilityViolation, EvidenceError, TaskLifecycleError) as exc:
            try:
                self._transition(
                    bind.task_id,
                    "RECOVERING",
                    event_ids,
                    reason=str(exc),
                    metadata={"evidence_refs": evidence_refs},
                )
                self._transition(
                    bind.task_id,
                    "QUARANTINED",
                    event_ids,
                    reason="read verification failed; operator review required",
                    metadata={"evidence_refs": evidence_refs},
                )
            # Best-effort quarantine recording: ignore TaskLifecycleError
            # when the task is already in a terminal state. The original
            # read failure above stays surfaced; a failed bookkeeping
            # transition should not mask it.
            except TaskLifecycleError:
                pass
            event_ids.append(
                self.audit.append(
                    "vesta",
                    "execution.failed",
                    {
                        "bind_id": bind.bind_id,
                        "task_id": bind.task_id,
                        "error": str(exc),
                        "evidence_refs": evidence_refs,
                    },
                ).seq
            )
            return {
                "status": "quarantined",
                "bind_id": bind.bind_id,
                "task_id": bind.task_id,
                "decision": decision.decision,
                "policy_version": decision.policy_version,
                "evidence_refs": evidence_refs,
                "audit_event_ids": event_ids,
                "error": str(exc),
            }
