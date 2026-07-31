"""Triumvirate router — Meta-Cognitive Planner surface."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body
from pydantic import BaseModel

from msb_v3.triumvirate.meta_cognitive_planner import MetaCognitivePlanner, PlanRequest

router = APIRouter(tags=["triumvirate"])
planner = MetaCognitivePlanner()


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
