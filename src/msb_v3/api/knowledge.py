"""Knowledge endpoints — extracted briefs from NotebookLM and sovereign stack research."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

_RESEARCH_ROOT = Path("/Users/lordwilson/msb-v3/runtime/research")


class RalphLoopBrief(BaseModel):
    name: str = "Ralph Loop"
    level: str = "6"
    purpose: str = "Recursive architecture for coordinating autonomous agents through deterministic state transitions, preventing invisible drift and hallucination."
    mechanism: str = "Agent reads STATUS.json, executes next_action, writes result back. No open-ended reasoning."
    guard_layers: list[str] = [
        "Scope Lock: integrity_locks.scope_hash hashes requirements; mismatch trips Circuit Breaker",
        "Budget Hard-Stop: constraints.budget_spent_usd kills loop when budget_cap_usd exceeded",
        "Stagnation Detector: hashes artifacts/ folder every step; unchanged for 3 loops trips Breaker",
    ]
    data_flow: list[str] = [
        "Goal Discovery: 5-stage recursive search generator identifies component gaps",
        "Telemetry Capture: physical action captured via sensors/logs",
        "Digital Ingestion: file enters /Inbox",
        "Deterministic Update: agent reads STATUS.json, processes file, updates integrity_locks",
    ]
    why_it_works: str = "Removes LLM autonomy to define success. Forces probabilistic models into deterministic JSON state transitions. Self-annealing via Sage/Argus agent diagnoses failures and reinforces protocol."


@router.get("/ralph-loop", response_model=RalphLoopBrief, tags=["knowledge"])
async def get_ralph_loop_brief() -> RalphLoopBrief:
    """Return the Ralph Loop architecture brief."""
    return RalphLoopBrief()


@router.get("/ralph-loop/dashboard/{loop_id}", tags=["knowledge"])
async def ralph_loop_dashboard(loop_id: str) -> dict:
    """Return the Ralph Loop dashboard: iterations, budget, integrity, governance."""
    from msb_v3.agent.ralph_loop import create_ralph_loop
    workdir = _RESEARCH_ROOT / f"ralph_{loop_id}"
    loop = create_ralph_loop(workdir=workdir)
    status = loop._read_status()
    return {
        "loop_id": loop_id,
        "status": status.status,
        "iterations": status.iterations,
        "budget_spent_usd": status.constraints.budget_spent_usd,
        "budget_cap_usd": status.constraints.budget_cap_usd,
        "budget_remaining_pct": round(
            max(0.0, (status.constraints.budget_cap_usd - status.constraints.budget_spent_usd) / status.constraints.budget_cap_usd * 100
        ), 2),
        "artifacts_hash": status.artifacts_hash[:16] if status.artifacts_hash else "",
        "integrity_locks": {
            "scope_hash": status.integrity_locks.scope_hash[:16] if status.integrity_locks.scope_hash else "",
            "mission_hash": status.integrity_locks.mission_hash[:16] if status.integrity_locks.mission_hash else "",
            "ethics_hash": status.integrity_locks.ethics_hash[:16] if status.integrity_locks.ethics_hash else "",
            "allowed_tools_hash": status.integrity_locks.allowed_tools_hash[:16] if status.integrity_locks.allowed_tools_hash else "",
        },
        "resources": {
            "prompt_tokens": status.resources.prompt_tokens,
            "completion_tokens": status.resources.completion_tokens,
            "cpu_seconds": round(status.resources.cpu_seconds, 3),
            "gpu_seconds": round(status.resources.gpu_seconds, 3),
        },
        "last_eval": status.result.get("last_eval"),
        "improvement_log": status.result.get("improvement_log", []),
        "logs": status.logs[-10:],
    }


@router.post("/ralph-loop/demo", tags=["knowledge"])
async def run_ralph_loop_demo() -> dict:
    """Run a 3-iteration Ralph Loop demo to verify harness behavior."""
    from msb_v3.agent.ralph_loop import create_ralph_loop

    loop = create_ralph_loop()

    def action_fn(goal: str, status, context):
        iteration = status.iterations
        artifact = loop._artifacts_dir / f"finding_{iteration:03d}.md"
        artifact.write_text(f"# Finding {iteration}\n\nGoal: {goal}\n")
        resources = {
            "prompt_tokens": 120 + iteration * 40,
            "completion_tokens": 80 + iteration * 30,
            "cpu_seconds": 0.1 * iteration,
            "gpu_seconds": 0.05 * iteration,
        }
        context["resources"] = resources
        return f"Wrote {artifact.name}", None

    result = loop.execute(
        "demo: validate ralph loop harness",
        action_fn=action_fn,
        session="demo",
    )
    return {
        "ok": result.ok,
        "event": result.event,
        "payload": result.payload,
        "telemetry": result.telemetry,
        "error": result.error,
    }


@router.get("/active-cluster", tags=["knowledge"])
async def get_active_cluster() -> dict:
    """Return the NotebookLM active cluster index."""
    import json
    from pathlib import Path
    p = Path("/Users/lordwilson/notebooklm-library-deep-dive/active-index.json")
    if p.exists():
        return {"notebooks": json.loads(p.read_text())}
    return {"detail": "not found"}
