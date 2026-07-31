"""Research assistant router."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path

from msb_v3.harnesses.research_assistant import SovereignResearchAssistant

router = APIRouter(tags=["research"])


class RunRequest(BaseModel):
    topic: str
    slug: Optional[str] = None
    sources: List[str] = []


@router.post("/assistant/run")
async def run_research(body: RunRequest) -> dict:
    sources = [Path(p) for p in body.sources if Path(p).exists()]
    assistant = SovereignResearchAssistant(topic=body.topic, slug=body.slug)
    result = assistant.run_full_pipeline(sources=sources)
    return result


@router.get("/assistant/preflight")
async def preflight() -> dict:
    return {"checks": {}, "passed": True, "failed": []}


@router.get("/assistant/latest")
async def latest() -> dict:
    return {"status": "no_artifacts"}


@router.get("/assistant/state")
async def assistant_state() -> dict:
    return {"status": "idle", "active_run": None}


@router.post("/assistant/runs/{slug}/complete")
async def complete_run(slug: str, body: dict) -> dict:
    return {"status": "ok", "message": f"Run {slug} marked complete.", "received": body}


@router.get("/assistant/runs")
async def list_runs() -> dict:
    return {"runs": []}


@router.get("/assistant/runs/{slug}")
async def get_run(slug: str) -> dict:
    return {"slug": slug, "status": "not_found"}


@router.post("/assistant/memory/append")
async def append_memory(body: dict) -> dict:
    return {"ok": True, "received": body}


@router.post("/assistant/runs/{slug}/review")
async def review_run(slug: str, body: dict) -> dict:
    return {"slug": slug, "decision": body.get("decision", "pending"), "notes": body.get("notes", "")}
