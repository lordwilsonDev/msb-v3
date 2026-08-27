"""Triumvirate router — Meta-Cognitive Planner + Mission Anchor surfaces."""

from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from msb_v3.core.container import ApplicationContainer, get_container_dep
from msb_v3.observability.audit import _MULCH_DB
from msb_v3.observability.metrics import (
    TRIUMVIRATE_AUDIT,
    TRIUMVIRATE_HIPPOCAMPUS,
    TRIUMVIRATE_LOCK,
    TRIUMVIRATE_MULTIMODAL,
    TRIUMVIRATE_PEER_OPS,
    TRIUMVIRATE_PLAN,
    TRIUMVIRATE_SCAN,
)
from msb_v3.retrieval.vector_store import VectorDocument
from msb_v3.speech.response import VoiceResponder
from msb_v3.triumvirate.hardware_sovereignty import PeerNode
from msb_v3.triumvirate.meta_cognitive_planner import PlanRequest
from msb_v3.triumvirate.multimodal_interfaces import (
    HapticHeartbeat,
    VisionClaw,
)

router = APIRouter(tags=["triumvirate"])

# Multimodal feature flag (fail-closed, matches OPENAI_API_KEY pattern):
# VisionClaw / HapticHeartbeat / SpeechFunctions currently return
# status="stub", and the dashboard already excludes stub calls from
# the TRIUMVIRATE_MULTIMODAL counter (audit discipline: stub calls
# must not inflate multimodal metrics). The /multimodal/* routes
# stay mounted — but until a real implementation ships they 503
# unless MSB_MULTIMODAL_ENABLED=1 is set explicitly. A real impl
# that stops returning status="stub" implies the gate should be
# opened; tests assert both sides.
_MULTIMODAL_DISABLED_DETAIL = (
    "multimodal interfaces are stub-backed; set MSB_MULTIMODAL_ENABLED=1 "
    "to mount VisionClaw / HapticHeartbeat / SpeechFunctions, or land a "
    "real implementation in src/msb_v3/triumvirate/multimodal_interfaces.py "
    "(stub -> real transitions should also flip this gate)."
)


def _multimodal_disabled() -> bool:
    """Read MSB_MULTIMODAL_ENABLED per request — cheap, and tests can
    monkeypatch os.environ."""
    return os.getenv("MSB_MULTIMODAL_ENABLED", "0") != "1"


class PlanRequestModel(BaseModel):
    goal: str
    parameters: Dict[str, Any] | None = None
    sources: list[str] | None = None


@router.post("/plan")
async def plan_goal(
    body: PlanRequestModel,
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    request = PlanRequest(
        goal=body.goal,
        parameters=body.parameters,
        sources=body.sources,
    )
    plan = container.planner.plan(request)
    status = "ok" if plan else "error"
    TRIUMVIRATE_PLAN.labels(status=status).inc()
    return {
        "slug": plan.slug,
        "goal": plan.goal,
        "signature": plan.signature,
        "started_at": plan.started_at,
        "finished_at": plan.finished_at,
        "stages": [
            {
                "stage": s.stage,
                "name": s.name,
                "status": s.status,
                "thought": s.thought,
                "latency_s": s.latency_s,
            }
            for s in plan.stages
        ],
        "action_queue": plan.action_queue,
        "star_dag": plan.star_dag,
        "model": plan.model,
    }


@router.post("/status/lock")
async def status_lock(
    body: Dict[str, Any],
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    goal = body.get("goal") or ""
    parameters = body.get("parameters")
    status = container.anchor.scope_lock(goal, parameters)
    TRIUMVIRATE_LOCK.labels(status="ok").inc()
    return status


@router.post("/status/update")
async def status_update(
    body: Dict[str, Any],
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    phase = body.get("phase") or "running"
    iteration_count = int(body.get("iteration_count") or 0)
    budget_spent_usd = float(body.get("budget_spent_usd") or 0.0)
    status = container.anchor.update(phase, iteration_count, budget_spent_usd)
    return status


@router.get("/status")
async def status_read(
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    return container.anchor.read()


@router.get("/status/verify")
async def status_verify(
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    return container.anchor.verify()


@router.get("/status/dashboard")
async def status_dashboard(
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    status = container.anchor.read()
    verify = container.anchor.verify()
    return {
        "goal": status.get("goal"),
        "phase": status.get("current_phase"),
        "valid": verify.get("valid", False),
        "scope_hash": verify.get("scope_hash"),
        "iteration_count": status.get("iteration_count", 0),
    }


@router.post("/guardian/scan")
async def guardian_scan(
    body: Dict[str, Any],
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    script = body.get("script") or ""
    report = container.guardian.scan_script(script)
    TRIUMVIRATE_SCAN.labels(risk=report.risk or "UNKNOWN").inc()
    return {
        "risk": report.risk,
        "findings": report.findings,
        "blocked": report.blocked,
    }


@router.post("/guardian/sbom/register")
async def guardian_sbom_register(
    body: Dict[str, Any],
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    server_id = body.get("server_id") or ""
    path = body.get("path") or ""
    entry = container.sbom.register(server_id, path, body.get("metadata"))
    return entry


@router.get("/guardian/sbom/{server_id}")
async def guardian_sbom_trusted(
    server_id: str,
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    return {"trusted": container.sbom.trusted(server_id)}


@router.post("/guardian/least-privilege")
async def guardian_least_privilege(
    body: Dict[str, Any],
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    token = body.get("agent_token") or ""
    scope = body.get("required_scope") or ""
    return {"allowed": container.guardian.enforce_least_privilege(token, scope)}


@router.post("/guardian/poison-pill/arm")
async def guardian_poison_arm(
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    return container.poison_pill.arm()


@router.post("/guardian/poison-pill/detonate")
async def guardian_poison_detonate(
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    return container.poison_pill.detonate()


@router.post("/argus/audit")
async def argus_audit(
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    result = container.argus.run()
    count = result.get("count", 0) if isinstance(result, dict) else 0
    bucket = "0" if count == 0 else "1-5" if count <= 5 else "6+"
    TRIUMVIRATE_AUDIT.labels(count_bucket=bucket).inc()
    return result


@router.get("/argus/mulch")
async def argus_mulch() -> Dict[str, Any]:
    import sqlite3

    with sqlite3.connect(_MULCH_DB) as conn:
        rows = conn.execute(
            "SELECT id, timestamp, component, finding_type, description, resolution_status FROM mulch_learnings ORDER BY timestamp DESC LIMIT 50"
        ).fetchall()
    return {
        "rows": [
            {
                "id": r[0],
                "timestamp": r[1],
                "component": r[2],
                "finding_type": r[3],
                "description": r[4],
                "resolution_status": r[5],
            }
            for r in rows
        ]
    }


@router.post("/argus/mulch/{mulch_id}/resolve")
async def argus_mulch_resolve(mulch_id: int) -> Dict[str, Any]:
    import sqlite3

    with sqlite3.connect(_MULCH_DB) as conn:
        cur = conn.execute(
            "UPDATE mulch_learnings SET resolution_status='resolved' WHERE id=?", (mulch_id,)
        )
        return {"ok": cur.rowcount == 1, "id": mulch_id}


@router.post("/cluster/peers")
async def cluster_register_peer(
    body: Dict[str, Any],
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    node = PeerNode(
        node_id=body.get("node_id") or "",
        host=body.get("host") or "",
        port=int(body.get("port") or 0),
        capacity=int(body.get("capacity") or 1),
        cluster_role=body.get("cluster_role") or "worker",
    )
    container.cluster_discovery.register_peer(node)
    TRIUMVIRATE_PEER_OPS.labels(op="register").inc()
    return {"ok": True, "node_id": node.node_id}


@router.get("/cluster/peers")
async def cluster_list_peers(
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    TRIUMVIRATE_PEER_OPS.labels(op="list").inc()
    return {"peers": container.cluster_discovery.peers()}


def _hippocampus_chunk_id(hit_id: str, doc_id: str) -> str:
    """Recover chunk_id from a hippocampus VectorDocument id.

    Upsert composes ``id = f"{doc_id}::{chunk_id}"`` and stores ``doc_id`` in
    ``source``, so dropping the ``source + "::"`` prefix yields chunk_id
    unambiguously (even if doc_id itself contains ``::``).
    """
    prefix = f"{doc_id}::"
    return hit_id[len(prefix) :] if hit_id.startswith(prefix) else hit_id


@router.post("/hippocampus/upsert")
async def hippocampus_upsert(
    body: Dict[str, Any],
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    doc_id = body.get("doc_id") or ""
    chunk_id = body.get("chunk_id") or ""
    document = VectorDocument(
        id=f"{doc_id}::{chunk_id}",
        text=body.get("text") or "",
        source=doc_id,
        metadata=body.get("metadata") or {},
        embedding=body.get("embedding") or [],
    )
    await container.hippocampus.index([document])
    TRIUMVIRATE_HIPPOCAMPUS.labels(op="upsert").inc()
    return {"ok": True, "doc_id": doc_id, "chunk_id": chunk_id}


@router.post("/hippocampus/search")
async def hippocampus_search(
    body: Dict[str, Any],
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    embedding = body.get("embedding") or []
    limit = int(body.get("limit") or 5)
    hits = await container.hippocampus.search(query="", query_embedding=embedding, limit=limit)
    TRIUMVIRATE_HIPPOCAMPUS.labels(op="search").inc()
    return {
        "results": [
            {
                "doc_id": hit.source,
                "chunk_id": _hippocampus_chunk_id(hit.id, hit.source),
                "text": hit.text,
                "score": hit.score,
                "metadata": hit.metadata,
            }
            for hit in hits
        ]
    }


@router.post("/multimodal/vision/capture")
async def vision_capture() -> Dict[str, Any]:
    if _multimodal_disabled():
        raise HTTPException(status_code=503, detail=_MULTIMODAL_DISABLED_DETAIL)
    result = VisionClaw().capture_screen()
    # Parked calls are not real work: don't let the metrics count a parked
    # payload as a delivered multimodal operation (audit: parked subsystems
    # must not inflate the dashboards). Counts resume automatically when a
    # real implementation stops returning status="parked".
    if result.get("status") != "parked":
        TRIUMVIRATE_MULTIMODAL.labels(interface="vision").inc()
    return result


@router.post("/multimodal/haptic/heartbeat")
async def haptic_heartbeat() -> Dict[str, Any]:
    if _multimodal_disabled():
        raise HTTPException(status_code=503, detail=_MULTIMODAL_DISABLED_DETAIL)
    result = HapticHeartbeat().poll_sac()
    if result.get("status") != "parked":  # parked calls are not real work
        TRIUMVIRATE_MULTIMODAL.labels(interface="haptic").inc()
    return result


@router.post("/multimodal/speech/command")
async def speech_command(body: Dict[str, Any]) -> Dict[str, Any]:
    """Speech command — real pipeline: intent → response → TTS.

    Accepts {"transcript": "..."} and runs through VoiceResponder:
    extract_intent → generate spoken response → TTS output.
    """
    if _multimodal_disabled():
        raise HTTPException(status_code=503, detail=_MULTIMODAL_DISABLED_DETAIL)
    transcript = body.get("transcript") or ""
    responder = VoiceResponder(speak_aloud=False)
    response = responder.respond_to_text(transcript)
    TRIUMVIRATE_MULTIMODAL.labels(interface="speech").inc()
    return response.to_dict()
