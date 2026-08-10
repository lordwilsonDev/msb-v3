"""Local AI — Ollama client + bounded tool loop."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

import httpx

from msb_v3.core.config import settings
from msb_v3.guardrails.fold import StepEnforcer


@dataclass
class LocalAIResponse:
    text: str
    model: str
    latency_s: float
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)


class LocalAIClient:
    """Thin wrapper around Ollama with tool-call awareness."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or settings.ollama_url
        self.model = settings.ollama_model
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

        t0 = time.perf_counter()
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=settings.request_timeout_s) as client:
                    resp = client.post(f"{self.base_url}/api/generate", json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                break
            except Exception as exc:
                last_exc = exc
                time.sleep(0.15 * (attempt + 1))
        else:
            raise ConnectionError(f"ollama unreachable: {self.base_url} ({last_exc})")
        latency = round(time.perf_counter() - t0, 4)

        text = data.get("response", "")
        tool_calls = data.get("tool_calls") or []
        return LocalAIResponse(text=text, model=self.model, latency_s=latency, tool_calls=tool_calls)

    def execute_tool_loop(
        self,
        query: str,
        *,
        system: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        max_steps: int = 4,
        max_tokens: int = 2048,
    ) -> LocalAIResponse:
        """Bounded tool-call loop via /api/generate.

        Returns the final assistant text after executing tool calls and feeding
        results back, up to `max_steps`.
        """
        if not tools:
            return self.generate(query, system=system, max_tokens=max_tokens)

        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": query})

        final_text = ""
        enforcer = StepEnforcer(required_steps=[], terminal_tools=frozenset([t["name"] for t in (tools or [])]))
        current_prompt = query
        for step_idx in range(max_steps):
            if step_idx > 0:
                history_parts = []
                for msg in messages:
                    role = msg["role"]
                    content = msg.get("content", "")
                    if role == "tool":
                        history_parts.append(f"[Tool Result]: {content}")
                    elif role == "assistant" and msg.get("tool_calls"):
                        tc_names = ", ".join(tc["function"]["name"] for tc in msg["tool_calls"])
                        history_parts.append(f"[Assistant called tools: {tc_names}] {content}")
                    elif role != "system":
                        history_parts.append(f"[{role.capitalize()}]: {content}")
                current_prompt = "\n".join(history_parts)

            resp = self.generate(current_prompt, system=system, tools=tools, max_tokens=max_tokens)
            final_text = resp.text
            tool_calls = resp.tool_calls
            if not tool_calls:
                break

            nudge = enforcer.check(tool_calls)
            if nudge is not None:
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
                enforcer.record(name, args)
                messages.append({"role": "tool", "content": result})

        return LocalAIResponse(text=final_text, model=self.model, latency_s=0.0, tool_calls=[])

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LocalAIResponse:
        """Chat completion via /api/chat, with optional tool definitions."""
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if tools:
            payload["tools"] = tools

        t0 = time.perf_counter()
        with httpx.Client(timeout=settings.request_timeout_s) as client:
            try:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise ConnectionError(f"ollama unreachable: {self.base_url} ({exc})")
            data = resp.json()
        latency = round(time.perf_counter() - t0, 4)

        msg = data.get("message", {}) or {}
        text = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls") or []
        return LocalAIResponse(text=text, model=self.model, latency_s=latency, tool_calls=tool_calls)
