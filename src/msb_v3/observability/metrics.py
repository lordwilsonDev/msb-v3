"""Observability — Prometheus metrics."""

from __future__ import annotations

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
RATE_LIMIT_REJECTIONS = Counter(
    "msb_v3_rate_limit_rejections_total",
    "Requests refused by the rate/batch guards, by limiter and reason",
    ["limiter", "reason"],
)
# Hybrid model router decisions (Phase 2). Defined here so the metric family
# is registered in the default Prometheus registry as soon as the app imports
# observability — the server's /metrics/prometheus lists it before the first
# router decision. Incremented by fabric.model_router.ModelRouter.decide().
# Explicit registration: prometheus_client counters are lazy (they only enter
# the default REGISTRY on first increment), so without this the family would
# stay invisible on /metrics/prometheus until the first decision.
ROUTER_DECISIONS = Counter(
    "msb_v3_router_decisions_total",
    "Model-router decisions",
    ["task_kind", "tier", "cause"],
)
# ActionGate verdicts (M2 governance observability). Every gate() call lands
# in exactly one bucket: allowed (SAFE), indeterminate (REVIEW — human
# approval required), denied (BLOCK), or failed (gate raised — fail-closed).
# Incremented by msb_v3.agent.safety.ActionGate. The "stub" state from the
# M2 spec does not exist for the ActionGate (no stub capabilities route
# through it) — it is covered by other metric families.
ACTIONGATE_DECISIONS = Counter(
    "msb_v3_actiongate_decisions_total",
    "ActionGate verdicts",
    ["verdict"],
)
# Evidence-receipt reconciliation counters: the structured audit log
# (logs/audit.jsonl) carries model_calls and moie_verdict per run, so the
# Prometheus scrape must expose the same two dimensions or the log and the
# scrape silently disagree. MODEL_CALLS accumulates the per-run count (a
# run's receipt states its total; the counter adds them). MOIE_VERDICTS
# counts the MoIE *pre-filter* verdict, distinct from ACTIONGATE_DECISIONS
# which counts the authorization verdict downstream — a BLOCK at MoIE vs a
# BLOCK at the ActionGate are otherwise indistinguishable in Prometheus but
# distinct in the receipt. Incremented by agent.handle._record_cycle at the
# same chokepoint that emits the receipt, so the two cannot diverge.
MODEL_CALLS = Counter(
    "msb_v3_model_calls_total",
    "Model calls made per run, accumulated across cycles",
    ["harness"],
)
MOIE_VERDICTS = Counter(
    "msb_v3_moie_verdicts_total",
    "MoIE pre-filter verdicts (BLOCK/CONDITIONAL/APPROVE/etc.)",
    ["verdict"],
)
# Executor reliability (M5 core-loop observability, 2026-08-17): a task that
# needed more than one attempt is a *retry*; a retried task that then
# succeeded is a *recovery*. Together with the failure matrix's bounded
# retry policy they make retries and recovery measurable, not anecdotal.
TASK_RETRIES = Counter(
    "msb_v3_task_retries_total",
    "Task attempts beyond the first (retries)",
    ["harness"],
)
TASK_RECOVERIES = Counter(
    "msb_v3_task_recoveries_total",
    "Tasks that succeeded after at least one retry",
    ["harness"],
)
from prometheus_client import REGISTRY as _REGISTRY  # noqa: E402

# Explicit registration: prometheus_client counters are lazy (they only enter
# the default REGISTRY on first increment), so without this the family stays
# invisible on /metrics/prometheus until the first decision. Idempotent:
# re-imports (reload, fresh app factories) are a no-op once registered.
try:
    _REGISTRY.register(ROUTER_DECISIONS)
    _REGISTRY.register(ACTIONGATE_DECISIONS)
    _REGISTRY.register(TASK_RETRIES)
    _REGISTRY.register(TASK_RECOVERIES)
    _REGISTRY.register(MODEL_CALLS)
    _REGISTRY.register(MOIE_VERDICTS)
except ValueError:
    pass  # already registered

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
    def retry(cls, harness: str) -> None:
        TASK_RETRIES.labels(harness=harness).inc()

    @classmethod
    def recovered(cls, harness: str) -> None:
        TASK_RECOVERIES.labels(harness=harness).inc()

    @classmethod
    def gauge_active(cls, delta: int = 0) -> None:
        current = ACTIVE_CONNECTIONS._value.get() if hasattr(ACTIVE_CONNECTIONS, "_value") else 0
        ACTIVE_CONNECTIONS.set(max(0, current + delta))
