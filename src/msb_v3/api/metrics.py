"""Metrics router — Prometheus scrape + JSON summary."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from msb_v3.observability.metrics import Metrics

router = APIRouter(tags=["metrics"])


@router.get("/")
async def metrics_json() -> Dict[str, Any]:
    return {
        "ready": Metrics._ready,
        "prometheus": "/metrics/prometheus",
        "ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }


@router.get("/prometheus", response_class=PlainTextResponse)
async def metrics_prometheus() -> str:
    # response_class=PlainTextResponse is load-bearing: a bare `str` return
    # would be serialized as a JSON-escaped string (application/json with
    # literal \n escapes), which is NOT the Prometheus text format — a
    # scraper (or the /console metrics strip) would fail to parse it. The
    # substring-based tests could not see this; the format is now explicit.
    from prometheus_client import generate_latest

    return generate_latest().decode("utf-8")
