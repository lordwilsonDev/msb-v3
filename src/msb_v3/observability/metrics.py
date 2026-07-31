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
TRIUMVIRATE_PLAN = Counter(
    "msb_v3_triumvirate_plan_total",
    "Triumvirate plan calls",
    ["status"],
)
TRIUMVIRATE_LOCK = Counter(
    "msb_v3_triumvirate_lock_total",
    "Triumvirate lock calls",
    ["status"],
)
TRIUMVIRATE_AUDIT = Counter(
    "msb_v3_triumvirate_audit_total",
    "Argus audit runs",
    ["count_bucket"],
)
TRIUMVIRATE_SCAN = Counter(
    "msb_v3_triumvirate_scan_total",
    "Guardian scan calls",
    ["risk"],
)
TRIUMVIRATE_PEER_OPS = Counter(
    "msb_v3_triumvirate_peer_ops_total",
    "Cluster peer operations",
    ["op"],
)
TRIUMVIRATE_HIPPOCAMPUS = Counter(
    "msb_v3_triumvirate_hippocampus_total",
    "Hippocampus upsert/search ops",
    ["op"],
)
TRIUMVIRATE_MULTIMODAL = Counter(
    "msb_v3_triumvirate_multimodal_total",
    "Multimodal interface calls",
    ["interface"],
)

# Ensure all Triumvirate metrics are registered in the default registry at import time.
for _metric in (
    TRIUMVIRATE_PLAN,
    TRIUMVIRATE_LOCK,
    TRIUMVIRATE_AUDIT,
    TRIUMVIRATE_SCAN,
    TRIUMVIRATE_PEER_OPS,
    TRIUMVIRATE_HIPPOCAMPUS,
    TRIUMVIRATE_MULTIMODAL,
):
    pass


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
