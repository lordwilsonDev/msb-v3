"""Discover routers shipped with msb_v3 for the app factory."""

from __future__ import annotations

from importlib.metadata import entry_points

from msb_v3.api.health import router as health_router
from msb_v3.api.memory import router as memory_router
from msb_v3.api.chat import router as chat_router
from msb_v3.api.metrics import router as metrics_router
from msb_v3.api.studio import router as studio_router
from msb_v3.api.system import router as system_router


REGISTRY = [
    {"router": health_router, "prefix": "/health", "tags": ["health"]},
    {"router": memory_router, "prefix": "/memory", "tags": ["memory"]},
    {"router": chat_router, "prefix": "/chat", "tags": ["chat"]},
    {"router": metrics_router, "prefix": "/metrics", "tags": ["metrics"]},
    {"router": studio_router, "prefix": None, "tags": ["studio"]},
    {"router": system_router, "prefix": "/system", "tags": ["system"]},
]
