"""API app factory — mounts routers, CORS, metrics, middleware."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from msb_v3.api.health import router as health_router
from msb_v3.api.memory import router as memory_router
from msb_v3.api.chat import router as chat_router
from msb_v3.api.metrics import router as metrics_router
from msb_v3.api.studio import router as studio_router
from msb_v3.api.system import router as system_router
from msb_v3.api.status import router as status_router
from msb_v3.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


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

    app.include_router(health_router, tags=["health"])
    app.include_router(memory_router, prefix="/memory", tags=["memory"])
    app.include_router(chat_router, tags=["chat"])
    app.include_router(metrics_router, prefix="/metrics", tags=["metrics"])
    app.include_router(studio_router, tags=["studio"])
    app.include_router(system_router, prefix="/system", tags=["system"])

    return app
