"""Local AI — llama.cpp client with same interface as Ollama client."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

import httpx

from msb_v3.core.config import settings


@dataclass
class LocalAIResponse:
    text: str
    model: str
    latency_s: float
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)


class LlamaCPPClient:
    """Thin wrapper around llama-server with tool-call awareness."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or getattr(settings, "llama_cpp_url", "http://127.0.0.1:8080")
        self.model = getattr(settings, "llama_cpp_model", "/Users/lordwilson/models/gemma-4-12b-it/gemma-4-12b-it-q4_k_m.gguf")
        self._tools: Dict[str, Callable[..., str]] = {}

    def register_tool(self, name: str, func: Callable[..., str]) -> None:
        self._tools[name] = func

    def tool(self, name: str) -> Callable[[Callable[..., str]], Callable[..., str]]:
        def decorator(func: Callable[..., str]) -> Callable[..., str]:
            self.register_tool(name, func)
            return func
        return decorator

    def run_tool(self, name: str, args: Dict[str, Any]) -> str:
        if name not in self._tools:
            return f"[tool-error] unknown tool: {name}"
        try:
            return self._tools[name](**args)
        except Exception as exc:
            return f"[tool-error] {name}: {exc}"

    def _chat_payload(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
        return payload

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LocalAIResponse:
        payload = self._chat_payload(messages, temperature=temperature, max_tokens=max_tokens, tools=tools)
        t0 = time.perf_counter()
        with httpx.Client(timeout=settings.request_timeout_s) as client:
            try:
                resp = client.post(f"{self.base_url}/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as exc:
                raise ConnectionError(f"llama.cpp unreachable: {self.base_url} ({exc})")
        latency = round(time.perf_counter() - t0, 4)

        msg = data.get("choices", [{}])[0].get("message", {}) or {}
        text = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls") or []
        return LocalAIResponse(text=text, model=self.model, latency_s=latency, tool_calls=tool_calls)

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LocalAIResponse:
        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, tools=tools, temperature=temperature, max_tokens=max_tokens)

    def execute_tool_loop(
        self,
        query: str,
        *,
        system: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        max_steps: int = 4,
        max_tokens: int = 2048,
    ) -> LocalAIResponse:
        if not tools:
            return self.generate(query, system=system, max_tokens=max_tokens)

        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": query})

        final_text = ""
        for _ in range(max_steps):
            resp = self.generate(query, system=system, tools=tools, max_tokens=max_tokens)
            final_text = resp.text
            tool_calls = resp.tool_calls
            if not tool_calls:
                break

            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": resp.text}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {"function": {"name": tc["function"]["name"], "arguments": tc["function"].get("arguments", {})}}
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)

            for tc in tool_calls:
                name = tc["function"]["name"]
                args = tc["function"].get("arguments", {}) or {}
                if not isinstance(args, dict):
                    args = {}
                result = self.run_tool(name, args)
                messages.append({"role": "tool", "content": result})

        return LocalAIResponse(text=final_text, model=self.model, latency_s=0.0, tool_calls=[])
