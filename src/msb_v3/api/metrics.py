"""Metrics router — Prometheus scrape + JSON summary."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Response

from msb_v3.core.config import settings
from msb_v3.local_ai.ollama import LocalAIClient
from msb_v3.observability.metrics import Metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics_json() -> Dict[str, Any]:
    return {
        "ready": Metrics._ready,
        "prometheus": "/metrics/prometheus",
        "ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
