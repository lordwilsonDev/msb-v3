"""Local AI — thin Ollama client with tool-call awareness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from msb_v3.core.config import settings


@dataclass
class LocalAIResponse:
    text: str
    model: str
    latency_s: float
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)


class LocalAIClient:
    """Thin wrapper around Ollama /api/generate."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or settings.ollama_url
        self.model = settings.ollama_model

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LocalAIResponse:
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools

        import time

        t0 = time.perf_counter()
        with httpx.Client(timeout=settings.request_timeout_s) as client:
            resp = client.post(f"{self.base_url}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
        latency = round(time.perf_counter() - t0, 4)

        text = data.get("response", "")
        return LocalAIResponse(text=text, model=self.model, latency_s=latency)
