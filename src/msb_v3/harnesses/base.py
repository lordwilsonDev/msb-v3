"""Harnesses — base protocol + chat harness backed by local AI."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from msb_v3.local_ai.ollama import LocalAIClient, LocalAIResponse


@dataclass(frozen=True)
class HarnessResult:
    ok: bool
    event: str
    payload: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    telemetry: Dict[str, Any] = field(default_factory=dict)


class BaseHarness(ABC):
    """All domain harnesses must return HarnessResult."""

    @abstractmethod
    def execute(self, query: str, context: Dict[str, Any] | None = None) -> HarnessResult:
        ...


class ChatHarness(BaseHarness):
    """Default local chat — prompt → Ollama → text."""

    def __init__(self, *, client: LocalAIClient | None = None) -> None:
        self.client = client or LocalAIClient()

    def execute(
        self,
        query: str,
        context: Dict[str, Any] | None = None,
    ) -> HarnessResult:
        system = context.get("system") if context else None
        tools = context.get("tools") if context else None
        try:
            resp = self.client.generate(
                query,
                system=system,
                tools=[t for t in tools] if tools else None,
            )
            return HarnessResult(
                ok=True,
                event="chat:completed",
                payload={
                    "query": query,
                    "text": resp.text,
                    "model": resp.model,
                },
                telemetry={"latency_s": resp.latency_s},
            )
        except Exception as exc:
            return HarnessResult(ok=False, event="chat:error", error=str(exc))
