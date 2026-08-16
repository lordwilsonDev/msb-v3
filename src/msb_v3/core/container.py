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

The cheap, side-effect-light services (planner, anchor, guardian, sbom,
poison-pill, argus, cluster discovery, hippocampus, event bus, identity,
memory store, conversation stub) are built eagerly. The three heavyweight,
settings-backed services — ``flywheel`` (FlywheelEngine), ``vesta``
(VestaServices), and ``spine`` (DecisionEvidenceStore, shared by both) — are
built lazily on first access so focused tests that only need e.g.
``hippocampus`` don't construct a full flywheel/vesta/spine stack against
the real settings paths.

All named module-level service singletons are now migrated. Any remaining
service construction is request-scoped (e.g. ``api/agent``) or a CLI-local
helper (``flywheel/cli``), not a shared module global.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import Request

from msb_v3.conversation.envelope import StubModel
from msb_v3.core.event_bus import EventBus
from msb_v3.core.identity import AgentIdentity
from msb_v3.evidence.spine import DecisionEvidenceStore
from msb_v3.flywheel.engine import FlywheelEngine
from msb_v3.memory.store import MemoryStore
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

# Imported lazily (see the ``vesta`` property) to break the import cycle
# api.chat -> core.container -> vesta.services -> vesta.adapter -> api.chat.
# The name is available to mypy under TYPE_CHECKING; at runtime the dataclass
# annotations are strings (``from __future__ import annotations``).
if TYPE_CHECKING:
    from msb_v3.vesta.services import VestaServices


@dataclass
class ApplicationContainer:
    """The services a request can depend on, all explicit and non-optional.

    Always construct via ``build_container()`` (which wires every field); tests
    that need a substituted service call ``build_container(service=...)`` so
    the remaining fields stay real rather than ``None``.

    ``flywheel`` and ``vesta`` are lazy properties over their ``_flywheel`` /
    ``_vesta`` holders: built on first access (and cached) so the default
    container doesn't construct a full flywheel/vesta stack until a route that
    needs it is actually hit.
    """

    planner: MetaCognitivePlanner
    anchor: MissionAnchor
    guardian: GuardianScanner
    sbom: SBOMRegistry
    poison_pill: PoisonPill
    argus: ArgusAuditor
    cluster_discovery: ClusterAwareDiscovery
    hippocampus: VectorStore
    event_bus: EventBus
    identity: AgentIdentity
    memory_store: MemoryStore
    conversation_stub: StubModel
    _flywheel: FlywheelEngine | None = field(default=None, repr=False)
    _vesta: VestaServices | None = field(default=None, repr=False)
    _spine: DecisionEvidenceStore | None = field(default=None, repr=False)

    @property
    def flywheel(self) -> FlywheelEngine:
        engine = self._flywheel
        if engine is None:
            engine = FlywheelEngine()
            self._flywheel = engine
        return engine

    @property
    def spine(self) -> DecisionEvidenceStore:
        """The one shared Evidence Spine store (completion blueprint Phase 2).

        Lazily built on first access and reused by both the Vesta perimeter and
        the agent handle path, so every governed decision in the process lands
        on a single hash-chained spine. When ``vesta`` was injected whole
        (a tmp-backed ``VestaServices`` in tests), its spine wins so the two
        remain the same instance.
        """
        store = self._spine
        if store is None:
            if self._vesta is not None:
                store = self._vesta.spine
                self._spine = store
            else:
                store = DecisionEvidenceStore()
                self._spine = store
        return store

    @property
    def vesta(self) -> VestaServices:
        services = self._vesta
        if services is None:
            from msb_v3.vesta.services import build_vesta_services

            services = build_vesta_services(spine=self.spine)
            self._vesta = services
        return services


def build_container(**overrides: Any) -> ApplicationContainer:
    """Composition root: construct the default services, then apply overrides.

    ``flywheel`` and ``vesta`` overrides are stored on the lazy holders so a
    test can inject a tmp-backed engine/perimeter without triggering the
    default (settings-backed) construction.
    """
    flywheel = overrides.pop("flywheel", None)
    vesta = overrides.pop("vesta", None)
    spine = overrides.pop("spine", None)
    # One shared memory store for the planners and the memory/graph/chat
    # routers — the planner's triumphirate session lives in the same store.
    memory_store = overrides.pop("memory_store", None) or MemoryStore()
    services: dict[str, Any] = {
        "planner": MetaCognitivePlanner(memory_store=memory_store),
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
        "event_bus": EventBus(),
        "identity": AgentIdentity(),
        "memory_store": memory_store,
        "conversation_stub": StubModel(),
    }
    services.update(overrides)
    return ApplicationContainer(_flywheel=flywheel, _vesta=vesta, _spine=spine, **services)


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
