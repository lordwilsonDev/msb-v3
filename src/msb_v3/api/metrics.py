"""Metrics router — Prometheus scrape + JSON summary."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from msb_v3.observability.metrics import Metrics

router = APIRouter(tags=["metrics"])


@router.get("/")
async def metrics_json() -> Dict[str, Any]:
    return {
        "ready": Metrics._ready,
        "prometheus": "/metrics/prometheus",
        "ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }


@router.get("/prometheus")
async def metrics_prometheus() -> str:
    from prometheus_client import generate_latest

    return generate_latest().decode("utf-8")
