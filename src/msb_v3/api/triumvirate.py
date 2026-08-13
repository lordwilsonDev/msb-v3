"""Triumvirate router — Meta-Cognitive Planner + Mission Anchor surfaces."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel

from msb_v3.observability.metrics import (
    TRIUMVIRATE_AUDIT,
    TRIUMVIRATE_HIPPOCAMPUS,
    TRIUMVIRATE_LOCK,
    TRIUMVIRATE_MULTIMODAL,
    TRIUMVIRATE_PEER_OPS,
    TRIUMVIRATE_PLAN,
    TRIUMVIRATE_SCAN,
)
from msb_v3.triumvirate.argus_auditor import _MULCH_DB, ArgusAuditor
from msb_v3.triumvirate.guardian_scanner import (
    GuardianScanner,
    PoisonPill,
    SBOMRegistry,
)
from msb_v3.triumvirate.hardware_sovereignty import (
    ClusterAwareDiscovery,
    DocumentChunk,
    PeerNode,
    VectorHippocampus,
)
from msb_v3.triumvirate.meta_cognitive_planner import MetaCognitivePlanner, PlanRequest
from msb_v3.triumvirate.mission_anchor import MissionAnchor
from msb_v3.triumvirate.multimodal_interfaces import (
    HapticHeartbeat,
    SpeechFunctions,
    VisionClaw,
)

router = APIRouter(tags=["triumvirate"])
planner = MetaCognitivePlanner()
anchor = MissionAnchor()
guardian = GuardianScanner()
sbom = SBOMRegistry()
poison_pill = PoisonPill()
argus = ArgusAuditor()
cluster_discovery = ClusterAwareDiscovery()
hippocampus = VectorHippocampus()


class PlanRequestModel(BaseModel):
    goal: str
    parameters: Dict[str, Any] | None = None
    sources: list[str] | None = None


@router.post("/plan")
async def plan_goal(body: PlanRequestModel) -> Dict[str, Any]:
    request = PlanRequest(
        goal=body.goal,
        parameters=body.parameters,
        sources=body.sources,
    )
    plan = planner.plan(request)
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
async def status_lock(body: Dict[str, Any]) -> Dict[str, Any]:
    goal = body.get("goal") or ""
    parameters = body.get("parameters")
    status = anchor.scope_lock(goal, parameters)
    TRIUMVIRATE_LOCK.labels(status="ok").inc()
    return status


@router.post("/status/update")
async def status_update(body: Dict[str, Any]) -> Dict[str, Any]:
    phase = body.get("phase") or "running"
    iteration_count = int(body.get("iteration_count") or 0)
    budget_spent_usd = float(body.get("budget_spent_usd") or 0.0)
    status = anchor.update(phase, iteration_count, budget_spent_usd)
    return status


@router.get("/status")
async def status_read() -> Dict[str, Any]:
    return anchor.read()


@router.get("/status/verify")
async def status_verify() -> Dict[str, Any]:
    return anchor.verify()


@router.get("/status/dashboard")
async def status_dashboard() -> Dict[str, Any]:
    status = anchor.read()
    verify = anchor.verify()
    return {
        "goal": status.get("goal"),
        "phase": status.get("current_phase"),
        "valid": verify.get("valid", False),
        "scope_hash": verify.get("scope_hash"),
        "iteration_count": status.get("iteration_count", 0),
    }


@router.post("/guardian/scan")
async def guardian_scan(body: Dict[str, Any]) -> Dict[str, Any]:
    script = body.get("script") or ""
    report = guardian.scan_script(script)
    TRIUMVIRATE_SCAN.labels(risk=report.risk or "UNKNOWN").inc()
    return {
        "risk": report.risk,
        "findings": report.findings,
        "blocked": report.blocked,
    }


@router.post("/guardian/sbom/register")
async def guardian_sbom_register(body: Dict[str, Any]) -> Dict[str, Any]:
    server_id = body.get("server_id") or ""
    path = body.get("path") or ""
    entry = sbom.register(server_id, path, body.get("metadata"))
    return entry


@router.get("/guardian/sbom/{server_id}")
async def guardian_sbom_trusted(server_id: str) -> Dict[str, Any]:
    return {"trusted": sbom.trusted(server_id)}


@router.post("/guardian/least-privilege")
async def guardian_least_privilege(body: Dict[str, Any]) -> Dict[str, Any]:
    token = body.get("agent_token") or ""
    scope = body.get("required_scope") or ""
    return {"allowed": guardian.enforce_least_privilege(token, scope)}


@router.post("/guardian/poison-pill/arm")
async def guardian_poison_arm() -> Dict[str, Any]:
    return poison_pill.arm()


@router.post("/guardian/poison-pill/detonate")
async def guardian_poison_detonate() -> Dict[str, Any]:
    return poison_pill.detonate()


@router.post("/argus/audit")
async def argus_audit() -> Dict[str, Any]:
    result = argus.run()
    count = result.get("count", 0) if isinstance(result, dict) else 0
    bucket = "0" if count == 0 else "1-5" if count <= 5 else "6+"
    TRIUMVIRATE_AUDIT.labels(count_bucket=bucket).inc()
    return result


@router.get("/argus/mulch")
async def argus_mulch() -> Dict[str, Any]:
    import sqlite3
    with sqlite3.connect(_MULCH_DB) as conn:
        rows = conn.execute("SELECT id, timestamp, component, finding_type, description, resolution_status FROM mulch_learnings ORDER BY timestamp DESC LIMIT 50").fetchall()
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
        cur = conn.execute("UPDATE mulch_learnings SET resolution_status='resolved' WHERE id=?", (mulch_id,))
        return {"ok": cur.rowcount == 1, "id": mulch_id}


@router.post("/cluster/peers")
async def cluster_register_peer(body: Dict[str, Any]) -> Dict[str, Any]:
    node = PeerNode(
        node_id=body.get("node_id") or "",
        host=body.get("host") or "",
        port=int(body.get("port") or 0),
        capacity=int(body.get("capacity") or 1),
        cluster_role=body.get("cluster_role") or "worker",
    )
    cluster_discovery.register_peer(node)
    TRIUMVIRATE_PEER_OPS.labels(op="register").inc()
    return {"ok": True, "node_id": node.node_id}


@router.get("/cluster/peers")
async def cluster_list_peers() -> Dict[str, Any]:
    TRIUMVIRATE_PEER_OPS.labels(op="list").inc()
    return {"peers": cluster_discovery.peers()}


@router.post("/hippocampus/upsert")
async def hippocampus_upsert(body: Dict[str, Any]) -> Dict[str, Any]:
    chunk = DocumentChunk(
        doc_id=body.get("doc_id") or "",
        chunk_id=body.get("chunk_id") or "",
        text=body.get("text") or "",
        embedding=body.get("embedding") or [],
        metadata=body.get("metadata") or {},
    )
    hippocampus.upsert(chunk)
    TRIUMVIRATE_HIPPOCAMPUS.labels(op="upsert").inc()
    return {"ok": True, "doc_id": chunk.doc_id, "chunk_id": chunk.chunk_id}


@router.post("/hippocampus/search")
async def hippocampus_search(body: Dict[str, Any]) -> Dict[str, Any]:
    embedding = body.get("embedding") or []
    limit = int(body.get("limit") or 5)
    results = hippocampus.search(embedding, limit=limit)
    TRIUMVIRATE_HIPPOCAMPUS.labels(op="search").inc()
    return {"results": results}


@router.post("/multimodal/vision/capture")
async def vision_capture() -> Dict[str, Any]:
    result = VisionClaw().capture_screen()
    # Stub calls are not real work: don't let the metrics count a stub
    # payload as a delivered multimodal operation (audit: stub subsystems
    # must not inflate the dashboards). Counts resume automatically when a
    # real implementation stops returning status="stub".
    if result.get("status") != "stub":
        TRIUMVIRATE_MULTIMODAL.labels(interface="vision").inc()
    return result


@router.post("/multimodal/haptic/heartbeat")
async def haptic_heartbeat() -> Dict[str, Any]:
    result = HapticHeartbeat().poll_sac()
    if result.get("status") != "stub":  # stub calls are not real work
        TRIUMVIRATE_MULTIMODAL.labels(interface="haptic").inc()
    return result


@router.post("/multimodal/speech/command")
async def speech_command(body: Dict[str, Any]) -> Dict[str, Any]:
    transcript = body.get("transcript") or ""
    result = SpeechFunctions().map_command(transcript)
    if result.get("status") != "stub":  # stub calls are not real work
        TRIUMVIRATE_MULTIMODAL.labels(interface="speech").inc()
    return result


