"""API app factory — mounts routers, CORS, metrics, middleware."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from msb_v3 import __version__
from msb_v3.api.agent import router as agent_router
from msb_v3.api.automation import router as automation_router
from msb_v3.api.chat import router as chat_router
from msb_v3.api.codegraph import router as codegraph_router
from msb_v3.api.console import router as console_router
from msb_v3.api.context import router as context_router
from msb_v3.api.conversation import router as conversation_router
from msb_v3.api.cron import router as cron_router
from msb_v3.api.dashboard import router as dashboard_router
from msb_v3.api.evolution import router as evolution_router
from msb_v3.api.factory import router as factory_router
from msb_v3.api.flywheel import router as flywheel_router
from msb_v3.api.governance import router as governance_router
from msb_v3.api.graph import router as graph_router
from msb_v3.api.health import router as health_router
from msb_v3.api.home import router as home_router
from msb_v3.api.hook import router as hook_router
from msb_v3.api.knowledge import router as knowledge_router
from msb_v3.api.mcp_bridge import router as mcp_router
from msb_v3.api.memory import router as memory_router
from msb_v3.api.memory_fabric import router as memory_fabric_router
from msb_v3.api.metrics import router as metrics_router
from msb_v3.api.models import router as models_router
from msb_v3.api.moie import router as moie_router
from msb_v3.api.notify import router as notify_router
from msb_v3.api.openai_compat import router as openai_compat_router
from msb_v3.api.ops_background import router as ops_background_router
from msb_v3.api.rag import router as rag_router
from msb_v3.api.redaction_middleware import SecretRedactionMiddleware
from msb_v3.api.research import router as research_router
from msb_v3.api.safety import router as safety_router
from msb_v3.api.skill_router import router as skill_router
from msb_v3.api.smi import router as smi_router
from msb_v3.api.studio import router as studio_router
from msb_v3.api.system import router as system_router
from msb_v3.api.tenants import router as tenants_router
from msb_v3.api.triumvirate import router as triumvirate_router
from msb_v3.api.wake import router as wake_router
from msb_v3.api.workflow import router as workflow_router
from msb_v3.business.registry import router as business_router
from msb_v3.core.config import settings
from msb_v3.core.container import get_container
from msb_v3.core.rate_limit import RateLimiter
from msb_v3.integrations.openbot import router as openbot_adapter_router
from msb_v3.node.api import router as node_router
from msb_v3.observability.metrics import RATE_LIMIT_REJECTIONS, SECRET_REDACTION_ARMED
from msb_v3.plei.api import plei_router
from msb_v3.secrets.selfcheck import SecretRedactionUnarmed, redaction_self_check
from msb_v3.vesta.api import router as vesta_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Record this boot in the audit chain (component=boot) so the
    # RootCauseEngine can detect restart events — the last link in the
    # resource-exhaustion chain (provider storm -> OOM restart). Best-effort:
    # a chain that refuses the append must never block startup.
    try:
        from msb_ledger.chain_anchor import anchored_chain_from_env

        anchored_chain_from_env().append(
            "boot", "boot.started", {"pid": __import__("os").getpid(), "version": __version__}
        )
    except Exception as exc:
        # Still best-effort — a chain that refuses the append must never block
        # startup — but never silent. This is the ledger's first record, and a
        # missing boot event is exactly what the restart detection it feeds would
        # need in order to notice anything. `pass` here made a broken chain
        # indistinguishable from a working one.
        logging.getLogger(__name__).warning(
            "boot record not written to the audit chain (%s: %s) — restart "
            "detection will not see this start",
            type(exc).__name__,
            exc,
        )

    # The heartbeat: an in-process cron scheduler (MSB_CRON_ENABLED=0 turns
    # it off; the CLI still runs jobs on demand). Started only when enabled
    # so tests (which disable it via the autouse conftest fixture) and
    # focused deployments never spawn a background loop they didn't ask for.
    scheduler_task = None
    if settings.cron_enabled:
        from msb_v3.cron.scheduler import CronScheduler

        # Runs until cancelled (the loop sleeps between ticks); shutdown
        # cancels it and waits for the current tick to unwind.
        scheduler_task = asyncio.create_task(CronScheduler().run_loop())
        # The 5-minute resident agent: seed the wake-agent cron job so the
        # loop exists by default (idempotent — only created when missing).
        if settings.wake_enabled:
            from msb_v3.wake.runner import ensure_wake_job

            ensure_wake_job()
        if settings.alert_check_enabled:
            from msb_v3.cron.actions import ensure_alert_check_job

            ensure_alert_check_job()
        if settings.model_keepalive_enabled:
            from msb_v3.cron.actions import ensure_model_keepalive_job

            ensure_model_keepalive_job()
    try:
        yield
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass


_RUN_RATE_LIMIT_WINDOW_S = 60
_RUN_RATE_LIMIT_MAX = 10
# /research/assistant/run is an expensive generative call; cap each client at
# _RUN_RATE_LIMIT_MAX requests per window (one unit per request).
_RUN_LIMITER = RateLimiter(
    window_s=lambda: _RUN_RATE_LIMIT_WINDOW_S,
    max_count=lambda: _RUN_RATE_LIMIT_MAX,
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="MSB v3",
        version=__version__,
        lifespan=lifespan,
    )

    # Composition root (Phase 1.4): one process-wide container, stashed on
    # app.state so request dependencies resolve it explicitly; routers mounted
    # on a bare FastAPI() fall back to the same process default.
    app.state.container = get_container()

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    allow_credentials = False if "*" in origins else True
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Secret redaction sits *inside* GZip: middleware added later wraps
    # middleware added earlier, so adding this one first means it inspects the
    # application's plain-text body and GZip compresses the redacted result
    # afterwards. Added after GZip it would receive compressed bytes.
    app.add_middleware(SecretRedactionMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # Arm the redactor for every configured secret before the first request, so
    # the value-based half of redaction bites even though nothing resolves keys
    # through the broker yet (no provider client is rewired to it) — and then
    # **refuse to start if a configured instance would mask nothing**. The
    # failure this prevents is not a crash: it is a process that starts
    # normally, serves traffic and masks nothing because its environment was not
    # the configured one. Reasons and verdicts: msb_v3.secrets.selfcheck.
    _status = redaction_self_check()
    SECRET_REDACTION_ARMED.set(_status.armed)
    _log = logging.getLogger(__name__)
    if not _status.ok:
        raise SecretRedactionUnarmed(_status.reason)
    if _status.verdict == "armed":
        _log.info(
            "secret redaction armed",
            extra={"structured_data": {"seeded": _status.armed, "verdict": _status.verdict}},
        )
    else:
        # Not a failure (fresh clone / CI / deliberate override) but never
        # silent: the gauge reports 0 and this line says why.
        _log.warning(
            "secret redaction is NOT active", extra={"structured_data": _status.as_dict()}
        )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or f"{time.time():.0f}"
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    @app.middleware("http")
    async def run_rate_limit_middleware(request: Request, call_next):
        if request.method == "POST" and request.url.path == "/research/assistant/run":
            if not _RUN_LIMITER.check(request, units=1):
                RATE_LIMIT_REJECTIONS.labels(limiter="run", reason="rate").inc()
                return Response(
                    content='{"detail":"rate_limit_exceeded"}',
                    status_code=429,
                    media_type="application/json",
                )
        return await call_next(request)

    app.include_router(health_router, tags=["health"])
    app.include_router(home_router, tags=["ui"])
    app.include_router(dashboard_router, tags=["cockpit"])
    app.include_router(memory_router, prefix="/memory", tags=["memory"])
    app.include_router(chat_router, tags=["chat"])
    app.include_router(metrics_router, prefix="/metrics", tags=["metrics"])
    app.include_router(studio_router, tags=["studio"])
    app.include_router(console_router, tags=["ui"])
    app.include_router(system_router, prefix="/system", tags=["system"])
    app.include_router(research_router, prefix="/research", tags=["research"])
    app.include_router(evolution_router, prefix="/evolution", tags=["evolution"])
    app.include_router(flywheel_router, tags=["flywheel"])
    app.include_router(governance_router, prefix="/governance", tags=["governance"])
    app.include_router(safety_router, prefix="/safety", tags=["safety"])
    app.include_router(notify_router, prefix="/notify", tags=["notify"])
    app.include_router(openbot_adapter_router)
    app.include_router(triumvirate_router, prefix="/triumvirate", tags=["triumvirate"])
    app.include_router(skill_router, prefix="/skills", tags=["skills"])
    app.include_router(models_router, prefix="/models", tags=["models"])
    app.include_router(knowledge_router, prefix="/knowledge", tags=["knowledge"])
    app.include_router(mcp_router, prefix="/mcp", tags=["mcp"])
    app.include_router(business_router, prefix="/business", tags=["business"])
    app.include_router(tenants_router, prefix="/tenants", tags=["tenants"])
    app.include_router(rag_router, prefix="/rag", tags=["rag"])
    app.include_router(graph_router, prefix="/graph", tags=["knowledge-graph"])
    app.include_router(codegraph_router, prefix="/codegraph", tags=["code-graph"])
    app.include_router(context_router, prefix="/context", tags=["context-engine"])
    app.include_router(moie_router, prefix="/moie", tags=["moie"])
    app.include_router(factory_router, prefix="/factory", tags=["factory"])
    app.include_router(memory_fabric_router, prefix="/memory-fabric", tags=["memory-fabric"])
    app.include_router(smi_router, prefix="/smi", tags=["smi"])
    app.include_router(conversation_router, prefix="/conversation", tags=["conversation"])
    app.include_router(workflow_router, prefix="/workflow", tags=["workflow"])
    app.include_router(openai_compat_router, prefix="/v1", tags=["openai"])
    app.include_router(cron_router, prefix="/cron", tags=["cron"])
    app.include_router(wake_router, prefix="/wake", tags=["wake"])
    app.include_router(automation_router, prefix="/automation", tags=["automation"])
    app.include_router(ops_background_router, prefix="/ops", tags=["ops"])
    app.include_router(hook_router, prefix="/hook", tags=["hook"])
    app.include_router(agent_router, prefix="/agent", tags=["agent"])
    app.include_router(node_router, prefix="/node/v1", tags=["sovereign-node"])
    app.include_router(vesta_router, prefix="/vesta", tags=["vesta"])
    app.include_router(plei_router, tags=["plei"])

    return app
