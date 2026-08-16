"""Agent router — the Handle-this slice exposed over HTTP (/agent).

The Dream Big Blue vertical slice (intent → plan → execute → verify →
evidence) runs end-to-end in this process via ``agent.handle.handle()``.
Phase 2 live test: a caller can drive a real router decision (R score,
frontier vs local) inside the server process, so the decision lands in
this server's Prometheus registry (/metrics/prometheus) — the slice was
previously only reachable from standalone scripts, whose in-process
metrics never appeared on the live server.

Gate: operator bearer token (Depends(require_operator), MSB_OPERATOR_TOKEN
from .env — fail-closed 503 until set, 401 on mismatch), the same control-
surface rule as /governance. Running the slice executes tools against the
vault tenant, so it is state-changing and must not be open.

Importing this module at server startup also registers the router's
Prometheus counter (model_router.ROUTER_DECISIONS) eagerly, so the metric
exists in /metrics/prometheus before the first decision.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from msb_v3.agent.handle import handle
from msb_v3.agent.paseo import PaseoMcpError
from msb_v3.api.auth import require_operator, require_operator_sse
from msb_v3.core.container import ApplicationContainer, get_container_dep

router = APIRouter(tags=["agent"])

_MAX_REQUEST_LEN = 2000


@router.post("/handle", dependencies=[Depends(require_operator)])
async def handle_slice(
    body: Dict[str, Any],
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    """Run the Handle-this slice end-to-end in this process.

    Body: {request: str, privacy?: bool, approve?: bool, tenant?: str,
           output_dir?: str, session?: str}. `privacy` overrides the
    interpreted intent's privacy flag (drives the router's privacy floor);
    None lets the model decide. Runs tools against the vault tenant, gated
    by the operator token.
    """
    request = body.get("request")
    if not isinstance(request, str) or not request.strip():
        raise HTTPException(status_code=422, detail="request is required (non-empty string)")
    if len(request) > _MAX_REQUEST_LEN:
        raise HTTPException(status_code=422, detail=f"request exceeds {_MAX_REQUEST_LEN} chars")

    privacy = body.get("privacy")
    if privacy is not None and not isinstance(privacy, bool):
        raise HTTPException(status_code=422, detail="privacy must be a boolean")

    approve = body.get("approve") or False
    tenant = body.get("tenant")
    if tenant is not None and not isinstance(tenant, str):
        raise HTTPException(status_code=422, detail="tenant must be a string")
    output_dir = body.get("output_dir")
    if output_dir is not None and not isinstance(output_dir, str):
        raise HTTPException(status_code=422, detail="output_dir must be a string")
    session = body.get("session") or "default"
    agent_id = body.get("agent_id")
    if agent_id is not None and not isinstance(agent_id, str):
        raise HTTPException(status_code=422, detail="agent_id must be a string")
    repo = body.get("repo")
    if repo is not None and not isinstance(repo, str):
        raise HTTPException(status_code=422, detail="repo must be a string")

    result = await handle(
        request,
        tenant=tenant or "wilson-vault",
        approve=bool(approve),
        output_dir=output_dir,
        session=str(session),
        privacy=privacy,
        agent_id=agent_id,
        repo=repo,
        spine=container.spine,
    )
    payload = {
        "ok": result.ok,
        "run_id": result.run_id,
        "verdict": result.verdict,
        "deterministic_hash": result.deterministic_hash,
        "error": result.error,
        "trace": result.trace,
    }
    if not result.ok:
        raise HTTPException(status_code=500, detail=payload)
    return payload


def _lifecycle() -> Any:
    from msb_v3.tasks.lifecycle import TaskLifecycle

    return TaskLifecycle()


@router.get("/providers", dependencies=[Depends(require_operator)])
async def list_providers() -> Dict[str, Any]:
    """Provider discovery (unified-architecture §7): the worker seams MSB
    can execute through, with live availability (CLI binaries on PATH)."""
    from msb_v3.agent.providers import ProviderRegistry

    providers = ProviderRegistry().list()
    return {"ok": True, "count": len(providers), "providers": providers}


@router.post("/register", dependencies=[Depends(require_operator)])
async def register_agent(body: Dict[str, Any]) -> Dict[str, Any]:
    """Register an agent identity (§17): a durable, capability-scoped grant.
    Fail-closed — an agent does only what its granted capabilities say."""
    from msb_v3.agent.identity import AgentIdentity, AgentRegistry

    agent_id = body.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise HTTPException(status_code=422, detail="agent_id is required")
    caps = body.get("granted_capabilities")
    if caps is not None and (not isinstance(caps, list) or not all(isinstance(c, str) for c in caps)):
        raise HTTPException(status_code=422, detail="granted_capabilities must be a list of strings")
    identity = AgentIdentity(
        agent_id=agent_id.strip(),
        name=str(body.get("name") or agent_id),
        kind=str(body.get("kind") or "local"),
        provider_id=str(body.get("provider_id") or "local.slice"),
        model=str(body.get("model") or "local"),
        granted_capabilities=tuple(caps or ()),
        tenant_scope=str(body.get("tenant_scope") or "*"),
        autonomy_level=int(body.get("autonomy_level") or 0),
        max_risk_tier=int(body.get("max_risk_tier") or 2),
    )
    return {"ok": True, "agent": AgentRegistry().register(identity)}


@router.get("/agents", dependencies=[Depends(require_operator)])
async def list_agents() -> Dict[str, Any]:
    from msb_v3.agent.identity import AgentRegistry

    agents = AgentRegistry().list()
    return {"ok": True, "count": len(agents), "agents": agents}


@router.get("/agents/{agent_id}", dependencies=[Depends(require_operator)])
async def get_agent(agent_id: str) -> Dict[str, Any]:
    from msb_v3.agent.identity import AgentRegistry

    try:
        return {"ok": True, "agent": AgentRegistry().get(agent_id).as_dict()}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_id}") from exc


@router.post("/agents/{agent_id}/revoke", dependencies=[Depends(require_operator)])
async def revoke_agent(agent_id: str, body: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Revoke an agent: its identity stays on record (auditable), but it can
    never run again until re-registered."""
    from msb_v3.agent.identity import AgentRegistry

    operator = str((body or {}).get("operator", "operator") or "operator")
    try:
        return {"ok": True, "agent": AgentRegistry().revoke(agent_id, operator)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown agent: {agent_id}") from exc


# --- Paseo execution surface (unified-architecture §7, docs/paseo-adapter-v1.md) ---
# Every operation is operator-gated: driving a Paseo worker creates
# worktrees, runs external agents, and can mutate repos — the same
# control-surface rule as /governance. Permission decisions are durable,
# operator-only, and forwarded exactly once (see agent.paseo.permissions).


def _paseo_adapter() -> Any:
    from msb_v3.agent.paseo import PaseoAdapter

    return PaseoAdapter()


def _paseo_broker() -> Any:
    from msb_v3.agent.paseo.permissions import PaseoPermissionBroker

    return PaseoPermissionBroker()


@router.post("/paseo/create", dependencies=[Depends(require_operator)])
async def paseo_create(body: Dict[str, Any]) -> Dict[str, Any]:
    """Spec ``create_task``: create a Paseo worker (optionally in an isolated
    git worktree) with an optional initial task."""
    cwd = body.get("cwd")
    title = body.get("title")
    if not isinstance(cwd, str) or not cwd.strip():
        raise HTTPException(status_code=422, detail="cwd is required")
    if not isinstance(title, str) or not title.strip():
        raise HTTPException(status_code=422, detail="title is required")
    task = body.get("task")
    if task is not None and not isinstance(task, str):
        raise HTTPException(status_code=422, detail="task must be a string")
    provider = str(body.get("provider") or "claude")
    result = await _paseo_adapter().create_task(
        cwd=cwd,
        title=title,
        task=task,
        provider=provider,
        model=body.get("model"),
        mode=body.get("mode"),
        worktree_name=body.get("worktree_name"),
        base_branch=body.get("base_branch"),
        background=bool(body.get("background", True)),
    )
    return {"ok": True, **result}


@router.post("/paseo/send", dependencies=[Depends(require_operator)])
async def paseo_send(body: Dict[str, Any]) -> Dict[str, Any]:
    """Spec ``send_task``: give a Paseo worker a task."""
    agent_id = body.get("agent_id")
    prompt = body.get("prompt")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise HTTPException(status_code=422, detail="agent_id is required")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(status_code=422, detail="prompt is required")
    result = await _paseo_adapter().send_task(
        agent_id,
        prompt,
        session_mode=body.get("session_mode"),
        background=bool(body.get("background", True)),
    )
    return {"ok": True, **result}


@router.get("/paseo/status/{agent_id}", dependencies=[Depends(require_operator)])
async def paseo_status(agent_id: str) -> Dict[str, Any]:
    """Spec ``monitor`` + ``retrieve_result``: the worker's current snapshot."""
    adapter = _paseo_adapter()
    return {"ok": True, "monitor": await adapter.monitor(agent_id), "result": await adapter.retrieve_result(agent_id)}


@router.get("/paseo/activity/{agent_id}", dependencies=[Depends(require_operator)])
async def paseo_activity(agent_id: str) -> Dict[str, Any]:
    """Rich status: the daemon's curated activity timeline for the worker.
    503 when the daemon is unreachable — honest, never a silent empty."""
    try:
        activity = await _paseo_adapter().activity(agent_id)
    except PaseoMcpError as exc:
        raise HTTPException(status_code=503, detail=f"paseo daemon unreachable: {exc}") from exc
    return {"ok": True, "agent_id": agent_id, **activity}


@router.post("/paseo/interrupt", dependencies=[Depends(require_operator)])
async def paseo_interrupt(body: Dict[str, Any]) -> Dict[str, Any]:
    """Spec ``interrupt``: abort the worker's current run (kill=true
    terminates the session permanently — reserved for operator revocation)."""
    agent_id = body.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise HTTPException(status_code=422, detail="agent_id is required")
    success = await _paseo_adapter().interrupt(agent_id, kill=bool(body.get("kill", False)))
    return {"ok": success, "agent_id": agent_id, "killed": bool(body.get("kill", False))}


@router.get("/paseo/permissions", dependencies=[Depends(require_operator)])
async def paseo_permissions() -> Dict[str, Any]:
    """All pending operator-gated permission decisions (parked runs)."""
    pending = _paseo_broker().pending()
    return {"ok": True, "count": len(pending), "permissions": pending}


@router.post("/paseo/permissions/{approval_id}/respond", dependencies=[Depends(require_operator)])
async def paseo_permission_respond(approval_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Operator decision on a parked permission. Records the decision and
    wakes the awaiting run, which forwards the response to the daemon itself
    (allow, or deny+interrupt so the worker stops). The worker never decides
    for itself."""
    approved = body.get("approved")
    if not isinstance(approved, bool):
        raise HTTPException(status_code=422, detail="approved must be a boolean")
    message = body.get("message")
    if message is not None and not isinstance(message, str):
        raise HTTPException(status_code=422, detail="message must be a string")
    operator = str(body.get("operator") or "operator")
    approval = await _paseo_broker().decide(approval_id, operator, approved, message or "")
    return {"ok": True, "approval": approval}


# --- SSE observation stream -------------------------------------------------

_SSE_TERMINAL = {"COMPLETED", "FAILED", "QUARANTINED", "DENIED"}
# Poll cadence: how long to wait on the live queue before checking the task
# state and emitting a heartbeat. Short enough that the `done` event tracks
# the terminal transition closely; doubles as the SSE keepalive.
_STREAM_POLL_S = 2.0


def _sse(event: str, data: Any) -> str:
    return "event: %s\ndata: %s\n\n" % (event, json.dumps(data, default=str))


async def _observation_stream(task_id: str, initial: Dict[str, Any]):
    """Replay the task's recorded observations, then stream new ones live.

    Emits ``event: observation`` per sample, ``event: done`` when the task
    reaches a terminal state, and a heartbeat comment every 15s to keep the
    connection alive. Subscriber cleanup is guaranteed on disconnect.
    """
    from msb_v3.tasks.observations import subscribe, unsubscribe

    for obs in initial.get("observations", []) or []:
        yield _sse("observation", obs)
    queue = subscribe(task_id)
    state = initial.get("state", "")
    try:
        while True:
            try:
                sample = await asyncio.wait_for(queue.get(), timeout=_STREAM_POLL_S)
                yield _sse("observation", sample)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
            # Terminal-state check runs on every poll (not just on a queue
            # item), so `done` tracks the run's completion closely.
            try:
                state = _lifecycle().get(task_id)["state"]
            except Exception:
                pass
            if state in _SSE_TERMINAL:
                yield _sse("done", {"state": state})
                break
    finally:
        unsubscribe(task_id, queue)


@router.get(
    "/tasks/{task_id}/observations/stream",
    dependencies=[Depends(require_operator_sse)],
)
async def stream_task_observations(task_id: str) -> StreamingResponse:
    """SSE live stream of a task's observations (replay + live).

    Dashboards watch an agent run as it happens: recorded observations are
    replayed first, then new ones stream in as the worker acts. Auth: bearer
    header (fetch clients) or ``?token=`` (EventSource — browsers cannot set
    headers). Unknown task -> 404; bad token -> 401.
    """
    from msb_v3.tasks.events import TaskLifecycleError

    try:
        record = _lifecycle().get(task_id)
    except TaskLifecycleError as exc:
        raise HTTPException(status_code=404, detail=f"unknown task: {task_id}") from exc
    return StreamingResponse(
        _observation_stream(
            task_id,
            {"observations": record["task"].get("observations", []), "state": record["state"]},
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/tasks", dependencies=[Depends(require_operator)])
async def list_tasks(limit: int = 25) -> Dict[str, Any]:
    """List unified tasks (event-sourced lifecycle, §27-28). Operator-gated:
    task bodies carry intent/plan content."""
    tasks = _lifecycle().list(limit=limit)
    return {"ok": True, "count": len(tasks), "tasks": tasks}


@router.get("/tasks/{task_id}", dependencies=[Depends(require_operator)])
async def get_task(task_id: str) -> Dict[str, Any]:
    """Full unified task: the §27 document + its event sequence."""
    from msb_v3.tasks.events import TaskLifecycleError

    try:
        return {"ok": True, **_lifecycle().get(task_id)}
    except TaskLifecycleError as exc:
        raise HTTPException(status_code=404, detail=f"unknown task: {task_id}") from exc


@router.get("/tasks/{task_id}/events", dependencies=[Depends(require_operator)])
async def get_task_events(task_id: str) -> Dict[str, Any]:
    """The task's event sequence (with audit-chain seq refs)."""
    from msb_v3.tasks.events import TaskLifecycleError

    try:
        return {"ok": True, "task_id": task_id, "events": _lifecycle().events(task_id)}
    except TaskLifecycleError as exc:
        raise HTTPException(status_code=404, detail=f"unknown task: {task_id}") from exc


@router.get("/tasks/{task_id}/replay", dependencies=[Depends(require_operator)])
async def replay_task(
    task_id: str,
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    """Event-sourced reconstruction (Phase 3): the task's derived state, its
    ordered event timeline, and its Evidence Spine decision trail — rebuilt
    from the event log, with any projection divergence surfaced. Operator-gated
    (task bodies carry intent/plan content)."""
    from msb_v3.tasks.events import TaskLifecycleError

    try:
        return {"ok": True, **container.replay.replay_task(task_id)}
    except TaskLifecycleError as exc:
        raise HTTPException(status_code=404, detail=f"unknown task: {task_id}") from exc
