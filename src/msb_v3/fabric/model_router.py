"""Local-only model router.

Every task routes to the local inference backend (Ollama / llama.cpp via
``local_ai.client_factory``). The remote frontier seam (DeepSeek /v1) was
retired on 2026-09-09 (decision D1, docs/blueprints/2026-09-09-production-hardening.md):
there is no remote tier anymore, so routing is deterministic and simple — the
same inputs always select the same local model. The ``RouterDecision`` record
and the Prometheus counter are kept so callers, the audit trail, and
dashboards keep working unchanged; ``tier`` is always ``"local"`` and
``available`` is always ``True`` (the local backend is the system's own).

    decision = ModelRouter().decide("plan")
    client, decision = resolve_client("plan")  # always the local factory client
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from msb_v3.core.config import settings
from msb_v3.observability.metrics import ROUTER_DECISIONS

# task_kind -> tier. All local after the frontier retirement (D1, 2026-09-09).
DEFAULT_TIER: Dict[str, str] = {
    "plan": "local",
    "verify_synth": "local",
    "classify": "local",
    "embed": "local",
    "route": "local",
    "routine_tool_call": "local",
    "chat": "local",
}

# Bounded cause label for the metrics counter (no free-form strings).
_CAUSE_LOCAL = "local-only"


@dataclass(frozen=True)
class RouterDecision:
    task_kind: str
    tier: str  # always "local" — the frontier tier was retired (D1, 2026-09-09)
    model: str
    reason: str
    privacy_scoped: bool
    available: bool = True  # the local backend is the system's own — always up

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ModelRouter:
    """Deterministic local-only router.

    ``decide()`` is a pure function: no I/O, same inputs -> same decision.
    ``available`` is always True (the local backend is always the intended
    target); callers execute against the returned client and surface their
    own failure path if the local backend is down.
    """

    def __init__(self, *, available: Optional[bool] = None) -> None:
        # Kept for callers/tests that pin availability; a False override is
        # honoured so degraded-execution paths stay testable. Defaults to
        # True — the local backend is the system's own.
        self._available_override = available

    # -- public API -----------------------------------------------------

    def decide(
        self,
        task_kind: str,
        *,
        privacy_scoped: bool = False,
        **_unused: Any,
    ) -> RouterDecision:
        """Route one task to the local backend.

        Deterministic: same inputs -> same decision. No I/O. ``privacy_scoped``
        is recorded on the decision (the slice's intents default privacy=True)
        but routing never changes — the local backend never leaves the device.
        """
        available = True if self._available_override is None else self._available_override
        reason = f"{task_kind} routes to the local backend (frontier retired 2026-09-09)"
        if privacy_scoped:
            reason = f"{task_kind} is privacy-scoped — local by construction (frontier retired 2026-09-09)"
        ROUTER_DECISIONS.labels(task_kind=task_kind, tier="local", cause=_CAUSE_LOCAL).inc()
        return RouterDecision(
            task_kind=task_kind,
            tier="local",
            model=settings.ollama_model,
            reason=reason,
            privacy_scoped=privacy_scoped,
            available=available,
        )


def resolve_client(
    task_kind: str,
    *,
    client: Optional[Any] = None,
    router: Optional[ModelRouter] = None,
    privacy_scoped: bool = False,
) -> tuple[Any, RouterDecision | None]:
    """Pick the local client for a task through the router.

    An injected `client` wins (tests and callers that already resolved a
    client); otherwise the router decides and returns (client, decision).
    The decision is always returned so callers can record/log it even when
    a client was injected. The client is always the local factory client —
    the frontier tier was retired (D1, 2026-09-09).
    """
    if router is None:
        router = ModelRouter()
    decision = router.decide(task_kind, privacy_scoped=privacy_scoped)
    if client is not None:
        return client, decision
    from msb_v3.local_ai.client_factory import get_client

    return get_client(), decision