"""Meta-Cognitive Planner engine for Triumvirate Phase 1."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings
from msb_v3.memory.store import MemoryStore, Message

_RUNTIME_ROOT = Path(settings.db_path).parent / "triumvirate"
_PLANNER_STATE_FILE = _RUNTIME_ROOT / "plan_state.json"
_MEMORY = MemoryStore()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:80] or "plan"


def _ensure_artifact_dirs(slug: str) -> Dict[str, Path]:
    root = _RUNTIME_ROOT / slug
    dirs = {
        "root": root,
        "stages": root / "stages",
        "artifacts": root / "artifacts",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def _append_memory(role: str, content: str, session: str = "triumvirate") -> None:
    try:
        _MEMORY.append(session, Message(role=role, content=content, ts=datetime.now(timezone.utc).timestamp()))
    except Exception as exc:
        logger.warning("triumvirate memory append failed: %s", exc)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def _goal_signature(goal: str, parameters: Optional[Dict[str, Any]] = None) -> str:
    raw = json.dumps({"goal": goal, "parameters": parameters or {}}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class PlanRequest:
    goal: str
    parameters: Optional[Dict[str, Any]] = None
    sources: Optional[List[str]] = None


@dataclass
class StageResult:
    stage: int
    name: str
    status: str
    output: Dict[str, Any]
    thought: str
    latency_s: float


@dataclass
class PlanArtifacts:
    slug: str
    goal: str
    signature: str
    started_at: str
    finished_at: str
    stages: List[StageResult]
    action_queue: List[Dict[str, Any]]
    star_dag: Dict[str, Any]
    model: str


class MetaCognitivePlanner:
    """Five-stage recursive planning engine."""

    def plan(self, request: PlanRequest) -> PlanArtifacts:
        started = _now_iso()
        slug = _slugify(request.goal)
        signature = _goal_signature(request.goal, request.parameters)
        dirs = _ensure_artifact_dirs(slug)
        stages: List[StageResult] = []

        def _save_state(status: str = "running") -> None:
            payload = {
                "slug": slug,
                "status": status,
                "goal": request.goal,
                "signature": signature,
                "started_at": started,
                "finished_at": _now_iso() if status != "running" else None,
                "current_stage": stages[-1].name if stages else None,
            }
            _write_json(_PLANNER_STATE_FILE, payload)

        # Stage 1: Goal Recognition
        stage1 = self._stage_goal_recognition(request.goal)
        stages.append(stage1)
        _append_memory("user", f"[triumvirate] goal: {request.goal}")
        _append_memory("assistant", f"[triumvirate] stage1={stage1.name}: {stage1.thought}")
        _write_json(dirs["stages"] / "01-goal-recognition.json", stage1.output)
        _save_state("running")

        # Stage 2: Inversion Critic
        stage2 = self._stage_inversion_critic(request.goal, stage1.output)
        stages.append(stage2)
        _append_memory("assistant", f"[triumvirate] stage2={stage2.name}: {stage2.thought}")
        _write_json(dirs["stages"] / "02-inversion-critic.json", stage2.output)

        # Stage 3: First Principles Synthesis
        stage3 = self._stage_first_principles(stage1.output, stage2.output)
        stages.append(stage3)
        _append_memory("assistant", f"[triumvirate] stage3={stage3.name}: {stage3.thought}")
        _write_json(dirs["stages"] / "03-first-principles.json", stage3.output)

        # Stage 4: Plan Schema Construction
        stage4 = self._stage_plan_schema(request.goal, stage3.output)
        stages.append(stage4)
        _append_memory("assistant", f"[triumvirate] stage4={stage4.name}: {stage4.thought}")
        _write_json(dirs["stages"] / "04-plan-schema.json", stage4.output)

        # Stage 5: Action Queue
        stage5 = self._stage_action_queue(stage4.output)
        stages.append(stage5)
        _append_memory("assistant", f"[triumvirate] stage5={stage5.name}: {stage5.thought}")
        _write_json(dirs["stages"] / "05-action-queue.json", stage5.output)

        finished = _now_iso()
        action_queue = stage5.output.get("queue", [])
        star_dag = stage4.output.get("star_dag", {"nodes": [], "edges": []})
        plan = PlanArtifacts(
            slug=slug,
            goal=request.goal,
            signature=signature,
            started_at=started,
            finished_at=finished,
            stages=stages,
            action_queue=action_queue,
            star_dag=star_dag,
            model=getattr(settings, "ollama_model", "unknown"),
        )
        _write_json(dirs["artifacts"] / "plan.json", self._serialize(plan))
        _write_json(dirs["root"] / "PLAN.json", self._serialize(plan))
        _save_state("completed")
        return plan

    def _stage_goal_recognition(self, goal: str) -> StageResult:
        t0 = datetime.now(timezone.utc).timestamp()
        thought = f"Goal recognition completed for '{goal}'."
        output: Dict[str, Any] = {
            "goal": goal,
            "capability_gaps": self._fallback_gaps(goal),
            "constraints": ["local-first", "deterministic", "sovereign"],
            "success_criteria": ["executable plan", "verifiable outcomes"],
            "model_text": thought,
        }
        return StageResult(stage=1, name="goal-recognition", status="ok", output=output, thought=thought, latency_s=round(datetime.now(timezone.utc).timestamp() - t0, 4))

    def _stage_inversion_critic(self, goal: str, prior: Dict[str, Any]) -> StageResult:
        t0 = datetime.now(timezone.utc).timestamp()
        assumptions = [
            "more automation always improves throughput",
            "centralized control is required for coordination",
            "external APIs are necessary for knowledge",
            "human review happens after execution",
            "failure should stop the pipeline",
        ]
        inversions = [
            "less automation can increase reliability and observability",
            "decentralized coordination reduces single points of failure",
            "local evidence and cached knowledge can replace external API calls",
            "human review can happen inline during execution",
            "failure can be a first-class signal, not a stop condition",
        ]
        leverage_points = [
            "local evidence grounding",
            "inline human review",
            "deterministic retry/backoff",
            "failure-aware scheduling",
        ]
        thought = f"Inverted {len(inversions)} default assumptions for '{goal}'."
        output = {
            "goal": goal,
            "assumptions": assumptions,
            "inversions": list(zip(assumptions, inversions)),
            "leverage_points": leverage_points,
            "model_text": thought,
        }
        return StageResult(stage=2, name="inversion-critic", status="ok", output=output, thought=thought, latency_s=round(datetime.now(timezone.utc).timestamp() - t0, 4))

    def _stage_first_principles(self, stage1: Dict[str, Any], stage2: Dict[str, Any]) -> StageResult:
        t0 = datetime.now(timezone.utc).timestamp()
        gaps = stage1.get("capability_gaps", [])
        dependencies = [
            {"from": "local-ai", "to": gap, "type": "provides"} for gap in gaps[:3]
        ] + [
            {"from": "memory-store", "to": "state-tracking", "type": "provides"},
            {"from": "fastapi-router", "to": "api-surface", "type": "exposes"},
        ]
        thought = "Mapped dependencies from existing sovereign stack to capability gaps."
        output = {
            "goal": stage1.get("goal"),
            "dependency_map": dependencies,
            "first_principles": ["local-first execution", "deterministic artifacts", "explicit state transitions"],
            "model_text": thought,
        }
        return StageResult(stage=3, name="first-principles", status="ok", output=output, thought=thought, latency_s=round(datetime.now(timezone.utc).timestamp() - t0, 4))

    def _stage_plan_schema(self, goal: str, prior: Dict[str, Any]) -> StageResult:
        t0 = datetime.now(timezone.utc).timestamp()
        timeline = {
            "now": ["initialize mission anchor", "verify local model execution"],
            "next": ["deploy guardian scanner", "wire sac monitoring"],
            "later": ["add vector hippocampus", "enable multimodal interfaces"],
        }
        star_dag = {
            "goal": goal,
            "nodes": [
                {"id": "n1", "label": "Mission Anchor", "phase": "now", "depends_on": []},
                {"id": "n2", "label": "Guardian Scanner", "phase": "next", "depends_on": ["n1"]},
                {"id": "n3", "label": "Vector Hippocampus", "phase": "later", "depends_on": ["n2"]},
            ],
            "edges": [
                {"from": "n1", "to": "n2"},
                {"from": "n2", "to": "n3"},
            ],
        }
        thought = "Built now/next/later timeline and STAR-compatible DAG."
        output = {"goal": goal, "timeline": timeline, "star_dag": star_dag, "model_text": thought}
        return StageResult(stage=4, name="plan-schema", status="ok", output=output, thought=thought, latency_s=round(datetime.now(timezone.utc).timestamp() - t0, 4))

    def _stage_action_queue(self, prior: Dict[str, Any]) -> StageResult:
        t0 = datetime.now(timezone.utc).timestamp()
        queue = [
            {"id": "a1", "action": "create_mission_anchor", "args": {"goal": prior.get("goal")}, "requires": []},
            {"id": "a2", "action": "run_guardian_scan", "args": {"target": "scripts"}, "requires": ["a1"]},
            {"id": "a3", "action": "verify_sbom", "args": {"server": "msb-mcp-server"}, "requires": ["a2"]},
            {"id": "a4", "action": "enable_least_privilege", "args": {"agent": "sub-agent"}, "requires": ["a2"]},
            {"id": "a5", "action": "deploy_poison_pill", "args": {"mode": "armed"}, "requires": ["a2", "a3", "a4"]},
        ]
        thought = f"Produced {len(queue)} queued actions."
        output = {"queue": queue, "count": len(queue), "model_text": thought}
        return StageResult(stage=5, name="action-queue", status="ok", output=output, thought=thought, latency_s=round(datetime.now(timezone.utc).timestamp() - t0, 4))

    def _fallback_gaps(self, goal: str) -> List[str]:
        lowered = goal.lower()
        if "security" in lowered or "guardian" in lowered:
            return ["script scanning", "sbom verification", "least privilege", "poison pill"]
        if "research" in lowered or "plan" in lowered:
            return ["goal decomposition", "inversion critic", "action queue"]
        return ["planning", "execution", "verification"]

    def _serialize(self, plan: PlanArtifacts) -> Dict[str, Any]:
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
                    "output": s.output,
                    "thought": s.thought,
                    "latency_s": s.latency_s,
                }
                for s in plan.stages
            ],
            "action_queue": plan.action_queue,
            "star_dag": plan.star_dag,
            "model": plan.model,
        }
