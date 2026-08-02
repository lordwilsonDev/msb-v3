"""API app factory — mounts routers, CORS, metrics, middleware."""

from __future__ import annotations

import time
from collections import defaultdict
from contextlib import asynccontextmanager
from ipaddress import ip_address
from threading import Lock
from typing import Dict, Tuple

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from msb_v3.api.health import router as health_router
from msb_v3.api.memory import router as memory_router
from msb_v3.api.chat import router as chat_router
from msb_v3.api.metrics import router as metrics_router
from msb_v3.api.studio import router as studio_router
from msb_v3.api.system import router as system_router
from msb_v3.api.status import router as status_router
from msb_v3.api.research import router as research_router
from msb_v3.api.safety import router as safety_router
from msb_v3.api.evolution import router as evolution_router
from msb_v3.api.notify import router as notify_router
from msb_v3.api.home import router as home_router
from msb_v3.api.triumvirate import router as triumvirate_router
from msb_v3.api.skill_router import router as skill_router
from msb_v3.api.models import router as models_router
from msb_v3.api.knowledge import router as knowledge_router
from msb_v3.api.mcp_bridge import router as mcp_router
from msb_v3.business.registry import router as business_router
from msb_v3.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


_RUN_RATE_LIMIT_WINDOW_S = 60
_RUN_RATE_LIMIT_MAX = 10
_RUN_RATE_WINDOW: Dict[str, Tuple[float, int]] = defaultdict(lambda: (0.0, 0))
_RUN_RATE_LOCK = Lock()


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    host = request.client.host if request.client else "unknown"
    try:
        return str(ip_address(host))
    except ValueError:
        return host


def create_app() -> FastAPI:
    app = FastAPI(
        title="MSB v3",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
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
            key = _client_key(request)
            with _RUN_RATE_LOCK:
                window_start, count = _RUN_RATE_WINDOW[key]
                now = time.time()
                if now - window_start > _RUN_RATE_LIMIT_WINDOW_S:
                    _RUN_RATE_WINDOW[key] = (now, 1)
                elif count >= _RUN_RATE_LIMIT_MAX:
                    return Response(content='{"detail":"rate_limit_exceeded"}', status_code=429, media_type="application/json")
                else:
                    _RUN_RATE_WINDOW[key] = (window_start, count + 1)
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

    return app
