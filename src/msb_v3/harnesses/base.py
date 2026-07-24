"""Harnesses — base protocol + chat harness backed by local AI."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List

from msb_v3.local_ai.ollama import LocalAIClient, LocalAIResponse


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
    def __init__(self, *, client: LocalAIClient | None = None) -> None:
        self.client = client or LocalAIClient()

    def execute(
        self,
        query: str,
        context: Dict[str, Any] | None = None,
        *,
        session: str = "default",
        **kwargs: Any,
    ) -> HarnessResult:
        context = context or {}
        hist = context.get("history")
        prompt = query
        if hist:
            prompt = f"{hist}\nUser: {query}"

        system = context.get("system")
        tools = context.get("tools")
        try:
            resp = self.client.generate(
                prompt,
                system=system,
                tools=[t for t in tools] if tools else None,
            )
            return HarnessResult(
                ok=True,
                event="chat:completed",
                payload={"query": query, "text": resp.text, "model": resp.model},
                telemetry={"latency_s": resp.latency_s, "session": session},
            )
        except Exception as exc:
            return HarnessResult(ok=False, event="chat:error", error=str(exc), telemetry={"session": session})
