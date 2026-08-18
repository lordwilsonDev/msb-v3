"""Meta-Cognitive Planner — honest intent pass-through.

Replaces the previous five-stage "recursive planning" engine, which emitted
static, hardcoded stage outputs and wrote five JSON files plus memory rows
per call without ever invoking a model or producing goal-dependent
reasoning. That was ceremony, not cognition.

The honest contract: planning is delegated to the real model-based planner
(``msb_v3.agent.planner``, used by ``agent/handle.py``). This module is a
thin, deterministic pass-through: it echoes the goal, records a signature,
and returns a single "proceed" action. It writes nothing to disk and appends
no memory — the ``/triumvirate/plan`` endpoint is a diagnostic intent echo,
not a planning engine.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:80] or "plan"


def _goal_signature(goal: str, parameters: Optional[Dict[str, Any]] = None) -> str:
    raw = json.dumps({"goal": goal, "parameters": parameters or {}}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class PlanRequest:
    """Goal + optional sources handed to the planner. Input shape for ``/triumvirate/plan``."""

    goal: str
    parameters: Optional[Dict[str, Any]] = None
    sources: Optional[List[str]] = None


@dataclass
class StageResult:
    """Output of one planner stage: status, payload, thought, timing."""

    stage: int
    name: str
    status: str
    output: Dict[str, Any]
    thought: str
    latency_s: float


@dataclass
class PlanArtifacts:
    """On-disk plan output: slug, goal hash, signature, timestamps."""

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
    """Intent pass-through planner.

    Deterministic and side-effect-free: echoes the goal, signs it, and
    returns a single "proceed" action. Real planning lives in
    ``msb_v3.agent.planner`` (the model-based planner used by ``handle()``).
    """

    def __init__(self, memory_store: Any = None) -> None:
        # ``memory_store`` is accepted for ApplicationContainer compatibility;
        # the pass-through intentionally does not write memory.
        self._memory = memory_store

    def plan(self, request: PlanRequest) -> PlanArtifacts:
        started = _now_iso()
        slug = _slugify(request.goal)
        signature = _goal_signature(request.goal, request.parameters)
        note = "planning is delegated to agent.planner; this endpoint is an intent echo"
        stage = StageResult(
            stage=1,
            name="intent-pass-through",
            status="ok",
            output={"goal": request.goal, "plan": ["proceed"], "note": note},
            thought=note,
            latency_s=0.0,
        )
        action_queue = [
            {"id": "a1", "action": "proceed", "args": {"goal": request.goal}, "requires": []}
        ]
        star_dag = {
            "goal": request.goal,
            "nodes": [{"id": "n1", "label": "proceed", "phase": "now", "depends_on": []}],
            "edges": [],
        }
        return PlanArtifacts(
            slug=slug,
            goal=request.goal,
            signature=signature,
            started_at=started,
            finished_at=_now_iso(),
            stages=[stage],
            action_queue=action_queue,
            star_dag=star_dag,
            model=getattr(settings, "ollama_model", "unknown"),
        )
