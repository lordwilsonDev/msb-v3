"""Observability — Prometheus metrics."""

from __future__ import annotations

from typing import Optional

import prometheus_client
from prometheus_client import Counter, Gauge, Histogram

QUERIES = Counter(
    "msb_v3_queries_total",
    "Total queries processed",
    ["harness", "event"],
)
DISPATCHER_EVENTS = Counter(
    "msb_v3_dispatcher_total",
    "Dispatcher events",
    ["dispatcher"],
)
LATENCY = Histogram(
    "msb_v3_latency_seconds",
    "Query latency in seconds",
    ["harness"],
)
READY = Gauge(
    "msb_v3_ready",
    "Sovereign core readiness (1=ready)",
)
ACTIVE_CONNECTIONS = Gauge(
    "msb_v3_active_connections",
    "Open HTTP connections",
)


class Metrics:
    _ready: bool = False

    @classmethod
    def set_ready(cls, value: bool) -> None:
        cls._ready = value
        READY.set(1 if value else 0)

    @classmethod
    def inc(cls, harness: str, event: str) -> None:
        QUERIES.labels(harness=harness, event=event).inc()

    @classmethod
    def inc_dispatcher(cls, dispatcher: str) -> None:
        DISPATCHER_EVENTS.labels(dispatcher=dispatcher).inc()

    @classmethod
    def latency(cls, harness: str, seconds: float) -> None:
        LATENCY.labels(harness=harness).observe(seconds)

    @classmethod
    def gauge_active(cls, delta: int = 0) -> None:
        current = ACTIVE_CONNECTIONS._value.get() if hasattr(ACTIVE_CONNECTIONS, "_value") else 0
        ACTIVE_CONNECTIONS.set(max(0, current + delta))
