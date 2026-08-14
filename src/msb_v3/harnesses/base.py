"""Harnesses — base protocol + chat harness backed by local AI."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict

from msb_v3.gateway import GatewayCall, GatewayContext, route
from msb_v3.local_ai.client_factory import active_backend, get_client
from msb_v3.local_ai.llama_client import LlamaCPPClient
from msb_v3.local_ai.ollama import LocalAIClient
from msb_v3.observability.metrics import Metrics

logger = logging.getLogger(__name__)

# Gateway wiring is opt-in by default: a ChatHarness call with no
# `requires_authorization` / `required_capabilities` in its context routes
# exactly as it did before the gateway existed (local backend), but the
# decision is recorded into the audit chain so the dispatcher is
# accountable. Callers who DO set the gate fields get the full
# authorization + capability check, and a denial returns a loud
# `chat:denied` result instead of a silent fallback (see
# docs/blueprints/plans/m1-governance-node-architecture.md §5).


@dataclass(frozen=True)
class HarnessResult:
    ok: bool
    event: str
    payload: Dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    telemetry: Dict[str, Any] = field(default_factory=dict)


class BaseHarness(ABC):
    @abstractmethod
    def execute(self, query: str, context: Dict[str, Any] | None = None, **kwargs: Any) -> HarnessResult:
        ...


class ChatHarness(BaseHarness):
    def __init__(self, *, client: LocalAIClient | LlamaCPPClient | None = None) -> None:
        self._client = client

    def use_factory_client(self) -> None:
        self._client = None

    @staticmethod
    def _fallback(query: str, system: str | None = None) -> tuple[str, dict]:
        text = f"[fallback] {query}"
        telemetry = {"dispatcher": "fallback", "model": "local-fallback"}
        return text, telemetry

    def execute(
        self,
        query: str,
        context: Dict[str, Any] | None = None,
        *,
        session: str = "default",
        **kwargs: Any,
    ) -> HarnessResult:
        context = context or {}
        system = context.get("system")
        tools = context.get("tools")

        # Capability Gateway: the runtime asks permission before it asks
        # compute. Opt-in gate fields (empty by default => no tightening,
        # identical routing to pre-gateway behavior) but the decision is
        # ALWAYS recorded to the audit chain so dispatcher choices are
        # replayable after the fact.
        decision = route(
            GatewayCall(
                name="chat.llm",
                estimated_bytes=int(context.get("estimated_bytes", 0)),
                capabilities=frozenset(context.get("required_capabilities", [])),
                requires_authorization=bool(context.get("requires_authorization", False)),
                metadata={
                    "slug": context.get("slug", "*"),
                    "session": session,
                },
            ),
            GatewayContext(
                granted_capabilities=frozenset(context.get("granted_capabilities", [])),
                granted_authorizations=frozenset(context.get("granted_authorizations", [])),
            ),
        )
        if not decision.authorized:
            # Denied = do not run, and do not silently fall back. A denial
            # is an event, not a degraded outcome — the caller needs to
            # know their request was refused and why.
            logger.warning(
                "chat.llm denied by gateway: %s (decision_id=%s)",
                decision.reason, decision.decision_id[:12],
            )
            return HarnessResult(
                ok=False,
                event="chat:denied",
                payload={"query": query, "text": f"[denied] {decision.reason}", "model": "gateway"},
                error=f"gateway_denied:{decision.reason}",
                telemetry={"decision_id": decision.decision_id, "session": session},
            )

        dispatcher = active_backend()
        client = self._client or get_client()
        started = time.perf_counter()
        try:
            resp = client.execute_tool_loop(
                query,
                system=system,
                tools=[t for t in tools] if tools else None,
            )
            elapsed = time.perf_counter() - started
            Metrics.inc_dispatcher(dispatcher)
            Metrics.inc("chat", "chat:completed")
            Metrics.latency("chat", elapsed)
            text = resp.text
            telemetry = {
                "latency_s": resp.latency_s,
                "session": session,
                "model": resp.model,
                "dispatcher": dispatcher,
                # Capability Gateway decision id — the audit-chain record
                # hash for THIS dispatch. Cross-reference to answer "why
                # did this call run where it did" after the fact.
                "decision_id": decision.decision_id,
                "gateway_reason": decision.reason,
                # Phase 1: cost logged per run — token counts ride the telemetry.
                "prompt_tokens": getattr(resp, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(resp, "completion_tokens", 0) or 0,
            }
            return HarnessResult(
                ok=True,
                event="chat:completed",
                payload={"query": query, "text": text, "model": resp.model},
                telemetry=telemetry,
            )
        except Exception:
            # Fallback path: it's tracked in metrics via chat:fallback, but
            # we also log here so the specific exception that triggered the
            # fallback (transient ollama outage, broken connection, etc.)
            # is visible in the normal log stream — the metric alone doesn't
            # tell you which failure mode it was.
            logger.debug("chat harness fallback path triggered", exc_info=True)
            elapsed = time.perf_counter() - started
            dispatcher = "fallback"
            Metrics.inc_dispatcher(dispatcher)
            Metrics.inc("chat", "chat:fallback")
            Metrics.latency("chat", elapsed)
            text, telemetry = self._fallback(query, system)
            return HarnessResult(
                ok=True,
                event="chat:completed",
                payload={"query": query, "text": text, "model": telemetry["model"]},
                error=None,
                telemetry={"session": session, **telemetry},
            )
