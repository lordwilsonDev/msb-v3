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
from msb_v3.api.research import router as research_router
from msb_v3.api.safety import router as safety_router
from msb_v3.api.evolution import router as evolution_router
from msb_v3.api.notify import router as notify_router
from msb_v3.api.home import router as home_router
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
    app.include_router(safety_router, prefix="/sac", tags=["safety"])
    app.include_router(safety_router, prefix="/echo", tags=["safety"])
    app.include_router(safety_router, prefix="/schh", tags=["safety"])
    app.include_router(safety_router, prefix="/systems-health", tags=["safety"])
    app.include_router(safety_router, prefix="/sn", tags=["safety"])
    app.include_router(evolution_router, prefix="/continuity", tags=["evolution"])
    app.include_router(evolution_router, prefix="/memory", tags=["evolution"])
    app.include_router(evolution_router, prefix="/mesh/discovery", tags=["evolution"])

    return app
