"""ApplicationContainer — the composition root (completion blueprint Phase 1.4).

Eliminates module-level service singletons: one place constructs every service
a request can depend on, so tests and alternate deployments substitute a
service explicitly instead of monkeypatching scattered module globals.

Pattern:

- ``build_container(**overrides)`` constructs a fresh container — the single
  place services are wired (the composition root).
- ``get_container()`` returns the process-wide default, lazily built.
- ``set_container()`` / ``reset_container()`` swap the default (test isolation).
- ``get_container_dep(request)`` is the FastAPI dependency: it prefers the
  request's ``app.state.container`` (set by ``create_app``) and falls back to
  the process default, so a router mounted on a bare ``FastAPI()`` still
  resolves.

Not yet in the container (next 1.4 increments): the vesta/api service singletons
(``_tasks``, ``_evidence``, ``_write_service``, …), ``api.memory`` /
``api.graph`` MemoryStore, ``api.flywheel`` engine, ``core.event_bus.bus`` and
``core.identity.identity`` — these are migrated incrementally under the same
composition-root pattern.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from msb_v3.observability.audit import ArgusAuditor
from msb_v3.retrieval.vector_store import VectorStore, get_vector_store
from msb_v3.triumvirate.guardian_scanner import (
    GuardianScanner,
    PoisonPill,
    SBOMRegistry,
)
from msb_v3.triumvirate.hardware_sovereignty import ClusterAwareDiscovery
from msb_v3.triumvirate.meta_cognitive_planner import MetaCognitivePlanner
from msb_v3.triumvirate.mission_anchor import MissionAnchor


@dataclass
class ApplicationContainer:
    """The services a request can depend on, all explicit and non-optional.

    Always construct via ``build_container()`` (which wires every field); tests
    that need a substituted service call ``build_container(service=...)`` so
    the remaining fields stay real rather than ``None``.
    """

    planner: MetaCognitivePlanner
    anchor: MissionAnchor
    guardian: GuardianScanner
    sbom: SBOMRegistry
    poison_pill: PoisonPill
    argus: ArgusAuditor
    cluster_discovery: ClusterAwareDiscovery
    hippocampus: VectorStore


def build_container(**overrides: Any) -> ApplicationContainer:
    """Composition root: construct the default services, then apply overrides."""
    services: dict[str, Any] = {
        "planner": MetaCognitivePlanner(),
        "anchor": MissionAnchor(),
        "guardian": GuardianScanner(),
        "sbom": SBOMRegistry(),
        "poison_pill": PoisonPill(),
        "argus": ArgusAuditor(),
        "cluster_discovery": ClusterAwareDiscovery(),
        # Hippocampus is the always-available sovereign memory: SQLite-backed
        # through the unified VectorStore interface so it never blocks on a
        # remote Qdrant (see retrieval/vector_store.py).
        "hippocampus": get_vector_store(backend="sqlite"),
    }
    services.update(overrides)
    return ApplicationContainer(**services)


_default: ApplicationContainer | None = None
_default_lock = threading.Lock()


def get_container() -> ApplicationContainer:
    """Process-wide default container, lazily built (composition root once)."""
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = build_container()
    return _default


def set_container(container: ApplicationContainer) -> None:
    """Replace the process default (test isolation / alternate deployment)."""
    global _default
    _default = container


def reset_container() -> None:
    """Drop the process default so the next ``get_container()`` rebuilds."""
    global _default
    _default = None


def get_container_dep(request: Request) -> ApplicationContainer:
    """FastAPI dependency: ``app.state.container`` wins, else the default.

    ``create_app`` stashes the container on ``app.state``; routers mounted on a
    bare ``FastAPI()`` (as in focused tests) fall back to the process default.
    """
    container = getattr(request.app.state, "container", None)
    return container if isinstance(container, ApplicationContainer) else get_container()
