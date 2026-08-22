"""Anthropic native Messages API client — the third harness behind the
AgentProvider ABC (api.anthropic), alongside DeepSeek (api.deepseek).

Anthropic's wire protocol is NOT OpenAI-compatible: messages go to
``/v1/messages`` with ``x-api-key`` + ``anthropic-version`` headers, the
system prompt is a top-level field (never a system message), tool calls are
``tool_use``/``tool_result`` content blocks, and usage is
``input_tokens``/``output_tokens``. This client owns that shape and exposes
the same duck-typed contract ``agent.handle()`` expects from a local client
(``generate``/``chat``/``execute_tool_loop`` + the tool registry), so a
governed run through ``AnthropicAgentProvider`` reuses every existing
mechanism — MoIE -> ActionGate -> evidence spine -> ledger -> receipt —
with zero new governance code (the same pattern as DeepSeek).

Circuit breaker (mirror of the DeepSeek client, Phase 0): a 402 (Payment
Required) or 429 (rate limit) opens a process-local circuit that
short-circuits all calls for a cooldown period — a dead API key must never
starve the server's thread pool.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

import httpx

from msb_v3.core.config import settings
from msb_v3.guardrails.fold import StepEnforcer
from msb_v3.local_ai.ollama import LocalAIResponse

logger = logging.getLogger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"

# Circuit breaker — process-local, opens on 402/429, clears on restart.
_circuit_open_at: float = 0.0
_circuit_reason: str = ""
_CIRCUIT_COOLDOWN_S = 300.0


def _circuit_is_open() -> bool:
    global _circuit_open_at
    if _circuit_open_at == 0.0:
        return False
    elapsed = time.monotonic() - _circuit_open_at
    if elapsed >= _CIRCUIT_COOLDOWN_S:
        _circuit_open_at = 0.0
        return False
    return True


def _open_circuit(reason: str) -> None:
    global _circuit_open_at, _circuit_reason
    if _circuit_open_at == 0.0:
        logger.warning("anthropic circuit opened: %s (cooldown %ss)", reason, _CIRCUIT_COOLDOWN_S)
    _circuit_open_at = time.monotonic()
    _circuit_reason = reason


def anthropic_circuit_state() -> Dict[str, Any]:
    """Read-only circuit state for observability (cockpit / health)."""
    open_ = _circuit_is_open()
    return {
        "open": open_,
        "reason": _circuit_reason if open_ else "",
        "cooldown_s": _CIRCUIT_COOLDOWN_S,
        "elapsed_s": round(time.monotonic() - _circuit_open_at, 1) if open_ else 0.0,
    }


def _tool_arguments(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a tool call's arguments to a dict (Anthropic sends a dict;
    accept a JSON string too for symmetry with the OpenAI shape)."""
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


def _to_anthropic_messages(messages: List[Dict[str, Any]]) -> tuple:
    """Split the OpenAI-ish messages array into (system, anthropic_messages).

    - system role -> top-level system string (Anthropic has no system role)
    - tool role  -> ``tool_result`` block inside a user turn (the id must be
      carried by the caller via ``tool_call_id``)
    - assistant with ``tool_calls`` -> text + ``tool_use`` content blocks
    """
    system_parts: List[str] = []
    out: List[Dict[str, Any]] = []
    for idx, m in enumerate(messages):
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            system_parts.append(str(content))
        elif role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(m.get("tool_call_id") or ""),
                            "content": str(content),
                        }
                    ],
                }
            )
        elif role == "assistant" and m.get("tool_calls"):
            blocks: List[Dict[str, Any]] = []
            if content:
                blocks.append({"type": "text", "text": str(content)})
            for i, tc in enumerate(m["tool_calls"]):
                fn = tc.get("function", {}) or {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(tc.get("id") or f"toolu_{idx}_{i}"),
                        "name": fn.get("name", ""),
                        "input": fn.get("arguments") or {},
                    }
                )
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": role if role in ("user", "assistant") else "user", "content": str(content)})
    return ("\n\n".join(system_parts)) or None, out


def _to_anthropic_tools(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """Convert OpenAI-ish tool schemas to Anthropic's shape."""
    if not tools:
        return None
    out = []
    for t in tools:
        fn = t.get("function", t) or {}
        out.append(
            {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or fn.get("input_schema") or {"type": "object", "properties": {}},
            }
        )
    return out


class AnthropicClient:
    """Anthropic Messages API client with the full governed-client contract:
    ``generate``, ``chat``, the bounded ``execute_tool_loop``, and the tool
    registry — a drop-in for ``LocalAIClient`` in ``agent.handle()`` on the
    Anthropic wire protocol."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_s: float = 120.0,
        transport: Any = None,
    ) -> None:
        self.base_url = (base_url or settings.anthropic_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.anthropic_api_key
        self.model = model or settings.anthropic_model
        self.timeout_s = timeout_s
        self._transport = transport
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

    # -- transport ----------------------------------------------------------

    def _post_messages(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST one Anthropic Messages completion (sync). Circuit breaker:
        a 402/429 opens the circuit so callers short-circuit instead of
        hammering a dead key."""
        if not self.api_key:
            raise RuntimeError("anthropic seam closed: ANTHROPIC_API_KEY not set")
        if _circuit_is_open():
            raise ConnectionError(
                f"anthropic circuit open: {_circuit_reason} (cooldown {_CIRCUIT_COOLDOWN_S}s)"
            )
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self._transport) as client:
                resp = client.post(
                    f"{self.base_url}/messages",
                    json=payload,
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": _ANTHROPIC_VERSION,
                        "content-type": "application/json",
                    },
                )
                if resp.status_code in (402, 429):
                    reason = f"HTTP {resp.status_code} ({'payment required' if resp.status_code == 402 else 'rate limit'})"
                    _open_circuit(reason)
                    raise ConnectionError(f"anthropic {reason}: {self.base_url}")
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise ConnectionError(f"anthropic unreachable: {self.base_url} ({exc})") from exc

    def _parse_response(self, data: Dict[str, Any], t0: float) -> LocalAIResponse:
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        tool_calls: List[Dict[str, Any]] = []
        for b in blocks:
            if b.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": b.get("id", ""),
                        "function": {"name": b.get("name", ""), "arguments": b.get("input", {})},
                    }
                )
        usage = data.get("usage") or {}
        return LocalAIResponse(
            text=text,
            model=self.model,
            latency_s=round(time.perf_counter() - t0, 4),
            tool_calls=tool_calls,
            prompt_tokens=int(usage.get("input_tokens", 0) or 0),
            completion_tokens=int(usage.get("output_tokens", 0) or 0),
        )

    # -- the governed-client contract ---------------------------------------

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LocalAIResponse:
        """Anthropic Messages completion over a messages array."""
        system, anthropic_messages = _to_anthropic_messages(messages)
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": anthropic_messages,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system
        anthropic_tools = _to_anthropic_tools(tools)
        if anthropic_tools:
            payload["tools"] = anthropic_tools
        t0 = time.perf_counter()
        data = self._post_messages(payload)
        return self._parse_response(data, t0)

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> LocalAIResponse:
        """One-shot completion (the intent/plan call shape handle() uses)."""
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
        """Bounded tool-call loop over Anthropic's tool_use/tool_result blocks
        (mirrors the local + DeepSeek loops; tool_use ids round-trip through
        the tool_result blocks)."""
        if not tools:
            return self.generate(query, system=system, max_tokens=max_tokens)

        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": query})

        final_text = ""
        enforcer = StepEnforcer(required_steps=[], terminal_tools=frozenset([t["name"] for t in tools]))
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
                        "id": tc.get("id", ""),
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"].get("arguments", {}),
                        },
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)

            for tc in tool_calls:
                name = tc["function"]["name"]
                args = _tool_arguments(tc)
                result = self.run_tool(name, args)
                enforcer.record(name, args)
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result})

        return LocalAIResponse(text=final_text, model=self.model, latency_s=0.0, tool_calls=[])


__all__ = ["AnthropicClient", "anthropic_circuit_state"]
