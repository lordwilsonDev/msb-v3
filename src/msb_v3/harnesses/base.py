"""Harnesses — base protocol + chat harness backed by local AI."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict

from msb_v3.local_ai.client_factory import active_backend, get_client
from msb_v3.local_ai.llama_client import LlamaCPPClient
from msb_v3.local_ai.ollama import LocalAIClient
from msb_v3.observability.metrics import Metrics


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
            }
            return HarnessResult(
                ok=True,
                event="chat:completed",
                payload={"query": query, "text": text, "model": resp.model},
                telemetry=telemetry,
            )
        except Exception:
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
