"""FastAPI surface for the Vesta trust/evidence perimeter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from msb_v3 import __version__
from msb_v3.api.auth import require_operator
from msb_v3.core.config import settings
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.node.filesystem import FileReader, FileWriter
from msb_v3.node.identity import IdentityStore, NodeAuthError, ReplayError
from msb_v3.node.models import EngageRequest
from msb_v3.observability.metrics import Metrics
from msb_v3.uac.audit_chain import AuditChain
from msb_v3.uac.chain_anchor import anchored_chain_from_env
from msb_v3.vesta.adapter import VestaMSBAdapter
from msb_v3.vesta.approvals import ApprovalError, VestaApprovalStore
from msb_v3.vesta.evidence import EvidenceError, EvidenceStore
from msb_v3.vesta.models import (
    ABind,
    VestaAuthorizeRequest,
    VestaChatRequest,
    VestaChatResponse,
    VestaFileReadRequest,
    VestaFileReadResponse,
    VestaFileWriteRequest,
    VestaShellRequest,
)
from msb_v3.vesta.policy import authorize_chat, capability_catalog
from msb_v3.vesta.read import VestaReadService
from msb_v3.vesta.runtime import TaskLifecycleError, VestaTaskStore
from msb_v3.vesta.shell import ShellExecutor, VestaShellApprovalStore, VestaShellService
from msb_v3.vesta.transport import TransportAdmission, require_vesta_transport
from msb_v3.vesta.write import VestaWriteService

# Router-level transport admission: when MSB_VESTA_REQUIRE_TUNNEL=1, the
# entire /vesta surface (including read-only status/discovery views) is
# reachable only from the allowed private peer CIDRs. Loopback is in the
# default allowed set, so local operations keep working.
router = APIRouter(tags=["vesta"], dependencies=[Depends(require_vesta_transport)])
# Anchored when MSB_CHAIN_ANCHOR_KEY is configured (T7 fix: the write-path
# chain re-anchors an external signed tip snapshot after every append, so a
# whole-audit-DB replacement is detectable); plain AuditChain otherwise.
_audit = anchored_chain_from_env()
_tasks = VestaTaskStore()
_evidence = EvidenceStore()
_adapter = VestaMSBAdapter(_audit, _tasks, _evidence)
_write_approvals = VestaApprovalStore()
_shell_approvals = VestaShellApprovalStore()
_node_root = Path(settings.node_sandbox_root)
if not _node_root.is_absolute():
    _node_root = Path(settings.msb_home) / _node_root
_node_db = Path(settings.node_db_path)
if not _node_db.is_absolute():
    _node_db = Path(settings.msb_home) / _node_db
_signed_identity = IdentityStore(
    str(_node_db),
    settings.node_pairing_code,
    session_ttl_s=settings.node_session_ttl_s,
    clock_skew_s=settings.node_clock_skew_s,
)
_read_service = VestaReadService(
    _audit,
    _tasks,
    _evidence,
    FileReader(_node_root, settings.node_max_read_bytes),
    KillSwitch(str(_node_db), audit_chain=_audit),
)
_shell_service = VestaShellService(
    _audit,
    _tasks,
    _evidence,
    _shell_approvals,
    ShellExecutor(
        _node_root,
        timeout_s=settings.vesta_shell_timeout_s,
        max_output_bytes=settings.vesta_shell_max_output_bytes,
    ),
    KillSwitch(str(_node_db), audit_chain=_audit),
)
_write_service = VestaWriteService(
    _audit,
    _tasks,
    _evidence,
    _write_approvals,
    FileWriter(_node_root, settings.node_max_read_bytes),
    KillSwitch(str(_node_db), audit_chain=_audit),
)


def _manifest() -> dict[str, Any]:
    return {
        "node_id": "vesta-local",
        "msb_version": __version__,
        "policy_version": "vesta-policy-1",
        "capability_profile": "local-sovereign-v1-phase-0-2",
        "integrity_status": "not_attested",
        "transport": "existing MSB process boundary; external tunnel deferred",
    }


@router.get("/status")
def status() -> dict[str, Any]:
    transport = TransportAdmission.from_settings()
    return {
        "service": "vesta",
        "status": "ACTIVE",
        "mode": "phase-0-2",
        "msb_version": __version__,
        "msb_ready": Metrics._ready,
        "policy_version": "vesta-policy-1",
        "ledger": "shared msb_v3.uac.AuditChain",
        "transport_required": transport.required,
        "transport_allowed_cidrs": [str(network) for network in transport.allowed_networks],
        "task_lifecycle": "durable-sqlite",
    }


@router.get("/msb-health")
def msb_health() -> dict[str, Any]:
    return {
        "service": "msb-v3",
        "version": __version__,
        "ready": Metrics._ready,
        "ollama_url": settings.ollama_url,
        "model": settings.ollama_model,
    }


@router.get("/manifest")
def manifest() -> dict[str, Any]:
    return _manifest()


@router.get("/capabilities")
def capabilities() -> dict[str, Any]:
    return {"policy_version": "vesta-policy-1", "capabilities": capability_catalog()}


@router.get("/routes")
def routes(request: Request) -> dict[str, Any]:
    # FastAPI 0.141 keeps included routers as lazy wrappers in app.routes;
    # OpenAPI is the stable flattened route registry for the live app.
    discovered = []
    for path, operations in request.app.openapi()["paths"].items():
        discovered.append({"path": path, "methods": sorted(operations)})
    return {"service": "msb-v3", "routes": sorted(discovered, key=lambda item: item["path"])}


@router.get("/ledger/verify")
def ledger_verify() -> dict[str, Any]:
    result = _audit.verify_chain()
    anchored = getattr(_audit, "verify_anchored", None)
    if anchored is not None:
        result["anchored"] = anchored()
    return result


@router.get("/tasks/{task_id}", dependencies=[Depends(require_operator)])
def task_status(task_id: str) -> dict[str, Any]:
    try:
        return _tasks.get(task_id)
    except TaskLifecycleError as exc:
        raise HTTPException(status_code=404, detail="unknown Vesta task") from exc


@router.get("/evidence/{evidence_id}", dependencies=[Depends(require_operator)])
def evidence_status(evidence_id: str) -> dict[str, Any]:
    try:
        return _evidence.get(evidence_id)
    except EvidenceError as exc:
        raise HTTPException(status_code=404, detail="unknown or invalid evidence object") from exc


@router.post(
    "/signed-chat",
    response_model=VestaChatResponse,
    dependencies=[Depends(require_vesta_transport)],
)
async def signed_chat(request: Request, body: EngageRequest) -> VestaChatResponse:
    try:
        device_id = _signed_identity.verify_request(body.model_dump())
    except NodeAuthError as exc:
        status_code = 409 if isinstance(exc, ReplayError) else 401
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    intent = body.intent
    target = intent.get("target")
    if intent.get("type") != "chat" or not isinstance(target, dict) or not isinstance(target.get("query"), str):
        raise HTTPException(status_code=422, detail="signed intent must be a chat intent with a query target")
    signed_body = VestaChatRequest(
        query=target["query"],
        session=body.session_id,
        capabilities=["model.inference", "memory.read"],
    )
    try:
        execution = await _adapter.execute_chat(request, signed_body, actor=device_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Vesta could not record or complete the signed MSB action") from exc
    if execution.decision.decision != "ALLOW" or execution.response is None:
        raise HTTPException(
            status_code=403,
            detail={
                "bind_id": execution.bind.bind_id,
                "task_id": execution.bind.task_id,
                "decision": execution.decision.decision,
                "reasons": list(execution.decision.reasons),
                "audit_event_ids": execution.audit_event_ids,
                "evidence_refs": execution.evidence_refs,
            },
        )
    return VestaChatResponse(
        ok=execution.response.ok,
        bind_id=execution.bind.bind_id,
        task_id=execution.bind.task_id,
        evidence_refs=execution.evidence_refs,
        decision=execution.decision.decision,
        policy_version=execution.decision.policy_version,
        payload=execution.response.payload.model_dump(),
        error=execution.response.error,
        audit_event_ids=execution.audit_event_ids,
    )


@router.post(
    "/read",
    response_model=VestaFileReadResponse,
    dependencies=[Depends(require_operator), Depends(require_vesta_transport)],
)
def read(body: VestaFileReadRequest) -> VestaFileReadResponse:
    return VestaFileReadResponse.model_validate(_read_service.execute(body))


@router.post(
    "/signed-read",
    response_model=VestaFileReadResponse,
    dependencies=[Depends(require_vesta_transport)],
)
def signed_read(body: EngageRequest) -> VestaFileReadResponse:
    try:
        device_id = _signed_identity.verify_request(body.model_dump())
    except NodeAuthError as exc:
        status_code = 409 if isinstance(exc, ReplayError) else 401
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    intent = body.intent
    target = intent.get("target")
    if intent.get("type") != "read_file" or not isinstance(target, dict) or not isinstance(target.get("path"), str):
        raise HTTPException(status_code=422, detail="signed intent must be a read_file intent with a path target")
    result = _read_service.execute(
        VestaFileReadRequest(session=body.session_id, path=target["path"]),
        actor=device_id,
    )
    return VestaFileReadResponse.model_validate(result)


@router.post(
    "/execute",
    dependencies=[Depends(require_operator), Depends(require_vesta_transport)],
)
def execute(body: VestaFileWriteRequest) -> dict[str, Any]:
    try:
        return _write_service.submit(body)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Vesta could not create the write approval") from exc


@router.post(
    "/shell/execute",
    dependencies=[Depends(require_operator), Depends(require_vesta_transport)],
)
def shell_execute(body: VestaShellRequest) -> dict[str, Any]:
    try:
        return _shell_service.submit(body)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Vesta could not create the shell approval") from exc


@router.get("/shell/approvals/{approval_id}", dependencies=[Depends(require_operator)])
def shell_approval_status(approval_id: str) -> dict[str, Any]:
    try:
        return _shell_approvals.get(approval_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="unknown shell approval") from exc


@router.post(
    "/shell/approvals/{approval_id}/approve",
    dependencies=[Depends(require_operator), Depends(require_vesta_transport)],
)
def shell_approve(approval_id: str) -> dict[str, Any]:
    try:
        return _shell_service.approve_and_execute(approval_id, "operator")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/shell/approvals/{approval_id}/signed-approve",
    dependencies=[Depends(require_vesta_transport)],
)
def shell_signed_approve(approval_id: str, body: EngageRequest) -> dict[str, Any]:
    """Accept one cryptographic owner ACK for one exact shell contract."""
    try:
        device_id = _signed_identity.verify_request(body.model_dump())
    except NodeAuthError as exc:
        status_code = 409 if isinstance(exc, ReplayError) else 401
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    intent = body.intent
    target = intent.get("target")
    if intent.get("type") != "shell_approval" or not isinstance(target, dict):
        raise HTTPException(status_code=422, detail="signed intent must be a shell_approval intent")
    if (
        target.get("approval_id") != approval_id
        or not isinstance(target.get("command_sha256"), str)
        or not isinstance(target.get("policy_version"), str)
    ):
        raise HTTPException(status_code=409, detail="signed approval target does not match the route")
    try:
        approval = _shell_approvals.get(approval_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="unknown shell approval") from exc
    if target["command_sha256"] != approval["command_sha256"]:
        raise HTTPException(status_code=409, detail="signed approval command hash does not match")
    if target["policy_version"] != approval["policy_version"]:
        raise HTTPException(status_code=409, detail="signed approval policy version does not match")
    try:
        return _shell_service.approve_and_execute(approval_id, device_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/shell/approvals/{approval_id}/reject",
    dependencies=[Depends(require_operator), Depends(require_vesta_transport)],
)
def shell_reject(approval_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    reason = str((body or {}).get("reason", "owner rejected shell execution"))
    try:
        return _shell_service.reject(approval_id, "operator", reason)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/approvals/{approval_id}", dependencies=[Depends(require_operator)])
def approval_status(approval_id: str) -> dict[str, Any]:
    try:
        return _write_approvals.get(approval_id)
    except ApprovalError as exc:
        raise HTTPException(status_code=404, detail="unknown approval") from exc


@router.post(
    "/approvals/{approval_id}/approve",
    dependencies=[Depends(require_operator), Depends(require_vesta_transport)],
)
def approve(approval_id: str) -> dict[str, Any]:
    try:
        return _write_service.approve_and_execute(approval_id, "operator")
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/approvals/{approval_id}/signed-approve",
    dependencies=[Depends(require_vesta_transport)],
)
def signed_write_approve(approval_id: str, body: EngageRequest) -> dict[str, Any]:
    """Accept one cryptographic owner ACK for one exact FILE_WRITE contract."""
    try:
        device_id = _signed_identity.verify_request(body.model_dump())
    except NodeAuthError as exc:
        status_code = 409 if isinstance(exc, ReplayError) else 401
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    intent = body.intent
    target = intent.get("target")
    if intent.get("type") != "file_write_approval" or not isinstance(target, dict):
        raise HTTPException(status_code=422, detail="signed intent must be a file_write_approval intent")
    if (
        target.get("approval_id") != approval_id
        or not isinstance(target.get("target_path"), str)
        or not isinstance(target.get("payload_sha256"), str)
        or not isinstance(target.get("expected_sha256"), str)
        or not isinstance(target.get("policy_version"), str)
    ):
        raise HTTPException(status_code=409, detail="signed write approval target is incomplete or mismatched")
    try:
        approval = _write_approvals.get(approval_id)
    except ApprovalError as exc:
        raise HTTPException(status_code=404, detail="unknown approval") from exc
    expected_sha256 = approval["expected_sha256"] or ""
    if (
        target["target_path"] != approval["target_path"]
        or target["payload_sha256"] != approval["payload_sha256"]
        or target["expected_sha256"] != expected_sha256
        or target["policy_version"] != approval["policy_version"]
    ):
        raise HTTPException(status_code=409, detail="signed write approval does not match the durable contract")
    try:
        return _write_service.approve_and_execute(approval_id, device_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/approvals/{approval_id}/reject",
    dependencies=[Depends(require_operator), Depends(require_vesta_transport)],
)
def reject(approval_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    reason = str((body or {}).get("reason", "owner rejected write"))
    try:
        return _write_service.reject(approval_id, "operator", reason)
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/authorize",
    dependencies=[Depends(require_operator), Depends(require_vesta_transport)],
)
def authorize(body: VestaAuthorizeRequest) -> dict[str, Any]:
    bind = ABind.create(body.session, body.capabilities)
    return {"bind": bind.as_dict(), "decision": authorize_chat(bind).as_dict()}


@router.post(
    "/chat",
    response_model=VestaChatResponse,
    dependencies=[Depends(require_operator), Depends(require_vesta_transport)],
)
async def chat(request: Request, body: VestaChatRequest) -> VestaChatResponse:
    try:
        execution = await _adapter.execute_chat(request, body)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Vesta could not record or complete the MSB action") from exc
    decision = execution.decision
    if decision.decision != "ALLOW":
        raise HTTPException(
            status_code=403,
            detail={
                "bind_id": execution.bind.bind_id,
                "task_id": execution.bind.task_id,
                "decision": decision.decision,
                "policy_version": decision.policy_version,
                "reasons": list(decision.reasons),
                "audit_event_ids": execution.audit_event_ids,
                "evidence_refs": execution.evidence_refs,
            },
        )
    if execution.response is None:
        raise HTTPException(status_code=503, detail="Vesta received no MSB response")
    return VestaChatResponse(
        ok=execution.response.ok,
        bind_id=execution.bind.bind_id,
        task_id=execution.bind.task_id,
        evidence_refs=execution.evidence_refs,
        decision=decision.decision,
        policy_version=decision.policy_version,
        payload=execution.response.payload.model_dump(),
        error=execution.response.error,
        audit_event_ids=execution.audit_event_ids,
    )
