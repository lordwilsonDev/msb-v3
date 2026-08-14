"""Vesta adapter around the existing MSB chat endpoint and AuditChain."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, List

from fastapi import Request

from msb_v3.api.chat import ChatRequest, ChatResponse
from msb_v3.api.chat import chat as msb_chat
from msb_v3.uac.audit_chain import AuditChain
from msb_v3.vesta.evidence import EvidenceStore
from msb_v3.vesta.models import ABind, VestaChatRequest
from msb_v3.vesta.policy import PolicyDecision, authorize_chat
from msb_v3.vesta.runtime import TaskLifecycleError, VestaTaskStore


@dataclass(frozen=True)
class VestaExecution:
    bind: ABind
    decision: PolicyDecision
    response: ChatResponse | None
    audit_event_ids: List[int]
    task_state: str
    evidence_refs: List[str]


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class VestaMSBAdapter:
    """Trust-plane adapter; MSB remains the sole reasoning/orchestration engine."""

    def __init__(
        self,
        audit: AuditChain,
        task_store: VestaTaskStore | None = None,
        evidence_store: EvidenceStore | None = None,
    ) -> None:
        self.audit = audit
        self.task_store = task_store or VestaTaskStore()
        self.evidence_store = evidence_store or EvidenceStore()

    def _transition(
        self,
        task_id: str,
        state: str,
        event_ids: List[int],
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = self.task_store.transition(task_id, state, reason=reason, metadata=metadata)
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

    async def execute_chat(
        self,
        request: Request,
        body: VestaChatRequest,
        *,
        actor: str = "operator",
    ) -> VestaExecution:
        bind = ABind.create(body.session, body.capabilities, actor=actor)
        request_evidence = self.evidence_store.record_json(
            {
                "bind_id": bind.bind_id,
                "task_id": bind.task_id,
                "session_id": bind.session_id,
                "query_sha256": _digest(body.query),
            },
            "vesta.request",
            {"bind_id": bind.bind_id, "task_id": bind.task_id},
        )
        evidence_refs = [request_evidence["evidence_id"]]
        self.task_store.create(
            bind,
            metadata={
                "query_sha256": _digest(body.query),
                "evidence_refs": evidence_refs,
            },
        )
        event_ids: List[int] = []
        event_ids.append(
            self.audit.append(
                "vesta",
                "request.received",
                {
                    "bind_id": bind.bind_id,
                    "task_id": bind.task_id,
                    "session_id": bind.session_id,
                    "actor": bind.actor,
                    "query_sha256": _digest(body.query),
                    "evidence_refs": evidence_refs,
                },
            ).seq
        )
        self._transition(bind.task_id, "AUTHENTICATED", event_ids, metadata={"evidence_refs": evidence_refs})
        self._transition(bind.task_id, "PLANNED", event_ids, metadata={"planner": "msb-chat-adapter"})
        decision = authorize_chat(bind)
        policy_evidence = self.evidence_store.record_json(
            decision.as_dict(),
            "vesta.policy_decision",
            {"bind_id": bind.bind_id, "task_id": bind.task_id},
        )
        evidence_refs.append(policy_evidence["evidence_id"])
        self.task_store.update_metadata(bind.task_id, {"evidence_refs": evidence_refs})
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
            event_ids.append(
                self.audit.append(
                    "vesta",
                    "request.denied",
                    {
                        "bind_id": bind.bind_id,
                        "task_id": bind.task_id,
                        "reasons": list(decision.reasons),
                        "evidence_refs": evidence_refs,
                    },
                ).seq
            )
            return VestaExecution(bind, decision, None, event_ids, "DENIED", evidence_refs)

        self._transition(bind.task_id, "AUTHORIZED", event_ids, metadata={"evidence_refs": evidence_refs})
        self._transition(bind.task_id, "EXECUTING", event_ids, metadata={"evidence_refs": evidence_refs})
        request.state.vesta_bind = bind.as_dict()
        try:
            response = await msb_chat(
                request,
                ChatRequest(query=body.query, session=body.session, system=body.system),
            )
            response_evidence = self.evidence_store.record_json(
                response.payload.model_dump(),
                "vesta.msb_response",
                {
                    "bind_id": bind.bind_id,
                    "task_id": bind.task_id,
                    "model": response.payload.model,
                },
            )
            evidence_refs.append(response_evidence["evidence_id"])
            self.task_store.update_metadata(bind.task_id, {"evidence_refs": evidence_refs})
            self._transition(
                bind.task_id,
                "VERIFYING",
                event_ids,
                metadata={"event": response.event, "evidence_refs": evidence_refs},
            )
        except Exception as exc:
            try:
                self._transition(
                    bind.task_id,
                    "RECOVERING",
                    event_ids,
                    reason=f"MSB adapter failure: {type(exc).__name__}",
                    metadata={"evidence_refs": evidence_refs},
                )
                self._transition(
                    bind.task_id,
                    "QUARANTINED",
                    event_ids,
                    reason="automatic recovery cannot prove a safe chat completion",
                    metadata={"evidence_refs": evidence_refs},
                )
            # Best-effort quarantine recording: ignore TaskLifecycleError
            # when the task is already in a terminal state. The original
            # chat failure above stays surfaced; a failed bookkeeping
            # transition should not mask it.
            except TaskLifecycleError:
                pass
            event_ids.append(
                self.audit.append(
                    "vesta",
                    "msb.failed",
                    {
                        "bind_id": bind.bind_id,
                        "task_id": bind.task_id,
                        "error_type": type(exc).__name__,
                        "evidence_refs": evidence_refs,
                    },
                ).seq
            )
            raise

        response_hash = _digest(response.payload.model_dump())
        event_ids.append(
            self.audit.append(
                "vesta",
                "msb.completed",
                {
                    "bind_id": bind.bind_id,
                    "task_id": bind.task_id,
                    "ok": response.ok,
                    "model": response.payload.model,
                    "response_sha256": response_hash,
                    "evidence_refs": evidence_refs,
                },
            ).seq
        )
        self._transition(
            bind.task_id,
            "COMPLETED",
            event_ids,
            metadata={"response_sha256": response_hash, "evidence_refs": evidence_refs},
        )
        event_ids.append(
            self.audit.append(
                "vesta",
                "response.returned",
                {
                    "bind_id": bind.bind_id,
                    "task_id": bind.task_id,
                    "event": response.event,
                    "evidence_refs": evidence_refs,
                },
            ).seq
        )
        return VestaExecution(bind, decision, response, event_ids, "COMPLETED", evidence_refs)
