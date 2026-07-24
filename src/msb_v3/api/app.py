"""API app factory — mounts routers, CORS, metrics, middleware."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from msb_v3.api.health import router as health_router
from msb_v3.api.chat import router as chat_router
from msb_v3.api.metrics import router as metrics_router
from msb_v3.api.studio import router as studio_router
from msb_v3.api.system import router as system_router
from msb_v3.core.config import settings
from msb_v3.local_ai.ollama import LocalAIClient
from msb_v3.observability.metrics import Metrics
from msb_v3.harnesses.base import ChatHarness


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    Metrics.set_ready(True)
    app.state.chat = ChatHarness(client=LocalAIClient())
    yield
    # shutdown
    Metrics.set_ready(False)


def create_app() -> FastAPI:
    app = FastAPI(title="MSB v3", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.cors_origins == "*" else [c.strip() for c in settings.cors_origins.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(metrics_router)
    app.include_router(studio_router)
    app.include_router(system_router)

    metrics_app = make_asgi_app()
    app.mount("/metrics/prometheus", metrics_app)

    return app
