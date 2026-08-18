"""DeepSeek native API client (OpenAI-compatible) — the first frontier
provider behind the AgentProvider ABC.

DeepSeek exposes an OpenAI-compatible ``/chat/completions`` surface, so this
client reuses ``FrontierClient``'s payload builder + response parser and adds
the missing half of the ``LocalAIClient`` contract: a ``chat(messages)`` method
plus a bounded ``execute_tool_loop``, so it can drive a full governed
``agent.handle()`` run (intent -> plan -> gated tool loop) instead of a single
completion.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List

import httpx

from msb_v3.core.config import settings
from msb_v3.fabric.model_router import FrontierClient
from msb_v3.guardrails.fold import StepEnforcer
from msb_v3.local_ai.ollama import LocalAIResponse


def _tool_arguments(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a tool call's arguments to a dict.

    OpenAI-compatible APIs encode ``function.arguments`` as a JSON *string*;
    Ollama returns a dict. Accept both so the governed tool receives real
    arguments on either surface.
    """
    raw = tool_call.get("function", {}).get("arguments", {}) or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


class DeepSeekClient(FrontierClient):
    """OpenAI-compatible DeepSeek client with the full governed-client
    contract: ``generate``/``agenerate`` (inherited), ``chat``, and the
    bounded tool loop. A drop-in for ``LocalAIClient`` in ``agent.handle()``.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_s: float = 120.0,
        transport: Any = None,
    ) -> None:
        super().__init__(
            base_url=base_url or settings.deepseek_base_url,
            api_key=api_key if api_key is not None else settings.deepseek_api_key,
            model=model or settings.deepseek_model,
            timeout_s=timeout_s,
            transport=transport,
        )
        self._tools: Dict[str, Callable[..., str]] = {}

    # -- tool registry (same contract as LocalAIClient) --------------------

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
        except Exception as exc:  # noqa: BLE001 — a tool error is a result, not a crash
            return f"[tool-error] {name}: {exc}"

    def _post_chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST one OpenAI-compatible chat completion (sync)."""
        if not self.api_key:
            raise RuntimeError("deepseek seam closed: DEEPSEEK_API_KEY not set")
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self._transport) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise ConnectionError(f"deepseek unreachable: {self.base_url} ({exc})") from exc

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LocalAIResponse:
        """OpenAI-compatible chat completion over a messages array."""
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
        t0 = time.perf_counter()
        data = self._post_chat(payload)
        return self._parse_response(data, t0)

    def execute_tool_loop(
        self,
        query: str,
        *,
        system: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        max_steps: int = 4,
        max_tokens: int = 2048,
    ) -> LocalAIResponse:
        """Bounded tool-call loop over the messages array (mirrors the local
        client's loop, but against DeepSeek's OpenAI-compatible endpoint)."""
        if not tools:
            return self.generate(query, system=system, max_tokens=max_tokens)

        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": query})

        final_text = ""
        enforcer = StepEnforcer(required_steps=[], terminal_tools=frozenset([t["name"] for t in (tools or [])]))
        for _step_idx in range(max_steps):
            resp = self.chat(messages, tools=tools, max_tokens=max_tokens)
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
                    {
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"].get("arguments", {}),
                        }
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)

            for tc in tool_calls:
                name = tc["function"]["name"]
                args = _tool_arguments(tc)
                result = self.run_tool(name, args)
                enforcer.record(name, args)
                messages.append({"role": "tool", "content": result})

        return LocalAIResponse(text=final_text, model=self.model, latency_s=0.0, tool_calls=[])
