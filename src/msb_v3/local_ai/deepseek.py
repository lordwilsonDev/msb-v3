"""DeepSeek native API client (OpenAI-compatible) — the first frontier
provider behind the AgentProvider ABC.

DeepSeek exposes an OpenAI-compatible ``/chat/completions`` surface, so this
client reuses ``FrontierClient``'s payload builder + response parser and adds
the missing half of the ``LocalAIClient`` contract: a ``chat(messages)`` method
plus a bounded ``execute_tool_loop``, so it can drive a full governed
``agent.handle()`` run (intent -> plan -> gated tool loop) instead of a single
completion.

Circuit breaker (Phase 0 — stabilize the organism): a 402 (Payment Required)
or 429 (rate limit) from the API opens a circuit that short-circuits all
calls for a cooldown period. Without this, the wake agent's 5-minute cron
job hammers a dead API key every cycle, each call blocking a worker thread
for up to ``timeout_s`` seconds and contributing to memory pressure / OOM
restarts. The circuit is process-local (not durable) — a restart clears it,
which is correct: the operator may have topped up the account.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List

import httpx

from msb_v3.core.config import settings
from msb_v3.fabric.model_router import FrontierClient
from msb_v3.guardrails.fold import StepEnforcer
from msb_v3.local_ai.ollama import LocalAIResponse

logger = logging.getLogger(__name__)

# Circuit breaker for the DeepSeek API. Opens on 402/429 (payment/rate-limit)
# and stays open for the cooldown, during which all calls raise immediately
# without touching the network. This prevents a dead API key from starving
# the server's thread pool. A process restart clears the circuit (the
# operator may have topped up the account between restarts).
_circuit_open_at: float = 0.0
_circuit_reason: str = ""
_CIRCUIT_COOLDOWN_S = 300.0  # 5 minutes — one wake cycle


def _circuit_is_open() -> bool:
    """True when the circuit is open and the cooldown hasn't elapsed."""
    global _circuit_open_at
    if _circuit_open_at == 0.0:
        return False
    elapsed = time.monotonic() - _circuit_open_at
    if elapsed >= _CIRCUIT_COOLDOWN_S:
        _circuit_open_at = 0.0
        return False
    return True


def _open_circuit(reason: str) -> None:
    """Open the circuit — subsequent calls short-circuit until the cooldown
    elapses or the process restarts."""
    global _circuit_open_at, _circuit_reason
    if _circuit_open_at == 0.0:
        logger.warning("deepseek circuit opened: %s (cooldown %ss)", reason, _CIRCUIT_COOLDOWN_S)
    _circuit_open_at = time.monotonic()
    _circuit_reason = reason


def deepseek_circuit_state() -> Dict[str, Any]:
    """Read-only circuit state for observability (cockpit / health)."""
    open_ = _circuit_is_open()
    return {
        "open": open_,
        "reason": _circuit_reason if open_ else "",
        "cooldown_s": _CIRCUIT_COOLDOWN_S,
        "elapsed_s": round(time.monotonic() - _circuit_open_at, 1) if open_ else 0.0,
    }


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
        """POST one OpenAI-compatible chat completion (sync).

        Circuit breaker: a 402 (Payment Required) or 429 (rate limit) opens
        the circuit — subsequent calls raise immediately without touching the
        network for the cooldown period. This prevents a dead API key from
        starving the server's thread pool (the wake agent's 5-minute cron was
        blocking a worker for up to 45s per call on every cycle).
        """
        if not self.api_key:
            raise RuntimeError("deepseek seam closed: DEEPSEEK_API_KEY not set")
        if _circuit_is_open():
            raise ConnectionError(
                f"deepseek circuit open: {_circuit_reason} "
                               f"(cooldown {_CIRCUIT_COOLDOWN_S}s)"
            )
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self._transport) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                # Circuit breaker: 402/429 are not transient — retrying every
                # 5 minutes wastes a worker thread and contributes to OOM.
                # Open the circuit so the wake agent short-circuits instead.
                if resp.status_code in (402, 429):
                    reason = f"HTTP {resp.status_code} ({'payment required' if resp.status_code == 402 else 'rate limit'})"
                    _open_circuit(reason)
                    raise ConnectionError(f"deepseek {reason}: {self.base_url}")
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
