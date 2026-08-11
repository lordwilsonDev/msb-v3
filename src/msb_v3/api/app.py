"""API app factory — mounts routers, CORS, metrics, middleware."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from msb_v3.api.chat import router as chat_router
from msb_v3.api.conversation import router as conversation_router
from msb_v3.api.evolution import router as evolution_router
from msb_v3.api.graph import router as graph_router
from msb_v3.api.health import router as health_router
from msb_v3.api.home import router as home_router
from msb_v3.api.knowledge import router as knowledge_router
from msb_v3.api.mcp_bridge import router as mcp_router
from msb_v3.api.memory import router as memory_router
from msb_v3.api.metrics import router as metrics_router
from msb_v3.api.models import router as models_router
from msb_v3.api.notify import router as notify_router
from msb_v3.api.openai_compat import router as openai_compat_router
from msb_v3.api.rag import router as rag_router
from msb_v3.api.research import router as research_router
from msb_v3.api.safety import router as safety_router
from msb_v3.api.skill_router import router as skill_router
from msb_v3.api.smi import router as smi_router
from msb_v3.api.studio import router as studio_router
from msb_v3.api.system import router as system_router
from msb_v3.api.tenants import router as tenants_router
from msb_v3.api.triumvirate import router as triumvirate_router
from msb_v3.api.workflow import router as workflow_router
from msb_v3.business.registry import router as business_router
from msb_v3.core.config import settings
from msb_v3.core.rate_limit import RateLimiter
from msb_v3.observability.metrics import RATE_LIMIT_REJECTIONS


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


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
        version="0.1.0",
        lifespan=lifespan,
    )

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    allow_credentials = False if "*" in origins else True
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=500)

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
    app.include_router(memory_router, prefix="/memory", tags=["memory"])
    app.include_router(chat_router, tags=["chat"])
    app.include_router(metrics_router, prefix="/metrics", tags=["metrics"])
    app.include_router(studio_router, tags=["studio"])
    app.include_router(system_router, prefix="/system", tags=["system"])
    app.include_router(research_router, prefix="/research", tags=["research"])
    app.include_router(evolution_router, prefix="/evolution", tags=["evolution"])
    app.include_router(safety_router, prefix="/safety", tags=["safety"])
    app.include_router(notify_router, prefix="/notify", tags=["notify"])
    app.include_router(triumvirate_router, prefix="/triumvirate", tags=["triumvirate"])
    app.include_router(skill_router, prefix="/skills", tags=["skills"])
    app.include_router(models_router, prefix="/models", tags=["models"])
    app.include_router(knowledge_router, prefix="/knowledge", tags=["knowledge"])
    app.include_router(mcp_router, prefix="/mcp", tags=["mcp"])
    app.include_router(business_router, prefix="/business", tags=["business"])
    app.include_router(tenants_router, prefix="/tenants", tags=["tenants"])
    app.include_router(rag_router, prefix="/rag", tags=["rag"])
    app.include_router(graph_router, prefix="/graph", tags=["knowledge-graph"])
    app.include_router(smi_router, prefix="/smi", tags=["smi"])
    app.include_router(conversation_router, prefix="/conversation", tags=["conversation"])
    app.include_router(workflow_router, prefix="/workflow", tags=["workflow"])
    app.include_router(openai_compat_router, prefix="/v1", tags=["openai"])

    return app
