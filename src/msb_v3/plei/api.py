"""PLEI API — FastAPI router under /plei.

Endpoints:
    GET  /plei/understand   → project summary
    GET  /plei/status       → lifecycle + health
    GET  /plei/lifecycle    → detailed lifecycle classification
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from msb_v3.plei.lifecycle import classify_lifecycle, lifecycle_as_dict
from msb_v3.plei.orchestrator import ingest_all, twin_summary

plei_router = APIRouter(prefix="/plei", tags=["plei"])

_DEFAULT_ROOT = str(Path(__file__).resolve().parents[3])  # repo root from src/msb_v3/plei


@plei_router.get("/understand", summary="Reconstruct project from evidence")
async def understand_project(project_root: str = Query(default=_DEFAULT_ROOT, description="Project root path")):
    """Ingest all seven layers and return the complete project understanding.

    This is the primary endpoint: README, source, tests, config, deps,
    evidence, and repo state — every assertion carries a provenance tag.
    """
    try:
        twin = ingest_all(project_root)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}") from exc
    return twin_summary(twin)


@plei_router.get("/status", summary="Lifecycle + project health")
async def project_status(project_root: str = Query(default=_DEFAULT_ROOT, description="Project root path")):
    """Return lifecycle position and health scores."""
    twin = ingest_all(project_root)
    lc = classify_lifecycle(twin)
    return {
        "lifecycle": lifecycle_as_dict(lc),
        "health": twin.health.scores.as_dict(),
        "risks": twin.health.risks.as_dict(),
        "missing_capabilities": twin.health.missing_capabilities.as_dict(),
    }


@plei_router.get("/lifecycle", summary="Detailed lifecycle classification")
async def project_lifecycle(project_root: str = Query(default=_DEFAULT_ROOT, description="Project root path")):
    """Classify lifecycle with full evidence and subsystem breakdown."""
    twin = ingest_all(project_root)
    lc = classify_lifecycle(twin)
    return lifecycle_as_dict(lc)