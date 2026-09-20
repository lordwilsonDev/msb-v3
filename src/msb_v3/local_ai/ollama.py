"""Local AI — Ollama client + bounded tool loop."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

import httpx

from msb_v3.core.config import settings
from msb_v3.guardrails.fold import StepEnforcer
from msb_v3.secrets.redact import redact, redact_obj


def _to_ollama_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert tool definitions from the repo's flat contract to Ollama's shape.

    The repo's internal tool contract is FLAT — ``{"type": "function",
    "name": ..., "description": ..., "parameters": ...}`` (``ToolSpec`` in
    ``api/chat.py``, ``ToolDef.as_model_schema`` in ``tools/registry.py``),
    because that is the shape ``tools.runtime.register_governed_tools`` reads a
    tool id out of. **Ollama does not accept it.** Sent verbatim, an entry in
    that shape is ignored: the request still returns 200, the model is never
    told the tool exists, and it therefore never calls it.

    Measured 2026-09-20 against Ollama 0.33.3 with one identical prompt and two
    otherwise-identical payloads: the nested entry returned
    ``vault_read({"path": "README.md"})``; the flat entry returned no
    ``tool_calls`` at all and a prose refusal. So the conversion belongs here,
    at the wire boundary, and the flat contract stays the repo's.

    Entries that are already nested pass through unchanged, so a caller holding
    the OpenAI shape (``anthropic.py`` accepts either) is never double-wrapped.
    """
    out: List[Dict[str, Any]] = []
    for tool in tools:
        if "function" in tool:
            out.append(tool)
            continue
        out.append(
            {
                "type": tool.get("type", "function"),
                "function": {
                    key: tool[key]
                    for key in ("name", "description", "parameters")
                    if key in tool
                },
            }
        )
    return out


def _strip_think(text: str) -> str:
    """Remove qwen3 <think>...</think> reasoning blocks from model output.

    The qwen3 template can emit these blocks (including empty ones) even when
    thinking is disabled; they are reasoning scratch-space and must never reach
    the caller as if they were the answer (chaos-test finding: the model once
    echoed its own /think control token as text).
    """
    if not text:
        return text
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


class OllamaTimeout(ConnectionError):
    """The model was reachable but did not finish inside ``request_timeout_s``.

    Subclasses ``ConnectionError`` deliberately. A read timeout and a refused
    connection need different *messages* but the same *handling*: every caller
    in the tree treats ConnectionError as "the brain is unavailable"
    (``api/automation.py`` turns it into a 503), and from a caller's point of
    view a model that will not answer in time is unavailable. Changing the type
    would silently turn that 503 into a 500 without making anything truer.

    The message is the point. Reporting a read timeout as "ollama unreachable"
    sent this lane's own diagnosis (JOB-022) hunting a network fault while the
    server was up and answering: the real cause was arithmetic — a
    ``num_predict`` budget larger than the timeout could cover at this model's
    measured throughput (see ``_ollama_error``).
    """

    def __init__(self, base_url: str, exc: BaseException) -> None:
        self.base_url = base_url
        self.timeout_s = settings.request_timeout_s
        super().__init__(
            f"ollama timed out after {self.timeout_s}s at {base_url} "
            f"({type(exc).__name__}: {exc})"
        )


def _ollama_error(base_url: str, exc: BaseException) -> Exception:
    """Name the failure for what it was, not for what is easiest to say.

    A timeout says how long was allowed, because the actionable question is
    always "is the budget too small or is the model too slow", not "is it up".
    Everything else keeps the existing wording, which callers and tests already
    match on.
    """
    if isinstance(exc, httpx.TimeoutException):
        return OllamaTimeout(base_url, exc)
    return ConnectionError(f"ollama unreachable: {base_url} ({exc})")


@dataclass
class LocalAIResponse:
    text: str
    model: str
    latency_s: float
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    # Token counts from the Ollama response (Phase 1: cost logged per run).
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LocalAIClient:
    """Thin wrapper around Ollama with tool-call awareness."""

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = base_url or settings.ollama_url
        # Explicit model override (e.g. a diverse reviewer panel serving a
        # distinct local model per reviewer); defaults to the configured one.
        self.model = model or settings.ollama_model
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
        # The *prompt* channel, at the wire boundary: a model may reason about a
        # credential's reference (`secret://env/...`) but never its value.
        # Placed here rather than in each caller so the flat-string path — which
        # skips the tool loop entirely — is covered too.
        prompt = redact(prompt)
        if system:
            system = redact(system)
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            # Pin thinking OFF explicitly. Ollama's qwen3 template appends the
            # "/think" control token to the last user message when think-mode
            # is left at its default, so the token leaks into the prompt the
            # model reads as text (chaos-test: "Say A" -> model confused by
            # "/think"). Explicit false => template sends /no_think instead.
            "think": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = _to_ollama_tools(tools)

        t0 = time.perf_counter()
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=settings.request_timeout_s) as client:
                    resp = client.post(f"{self.base_url}/api/generate", json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                break
            except httpx.TimeoutException as exc:
                # Do NOT retry a timeout. The retry loop exists for transient
                # refusals (daemon restarting, connection reset) where a second
                # attempt genuinely helps. A generation that outran its budget
                # will outrun it again, so retrying spends another full budget
                # of silence before reporting the same thing: with the 60s
                # default and three attempts, a caller waits ~180s to learn
                # what one attempt already knew.
                raise _ollama_error(self.base_url, exc) from exc
            except Exception as exc:
                last_exc = exc
                time.sleep(0.15 * (attempt + 1))
        else:
            final_exc = last_exc if last_exc is not None else RuntimeError("no attempt ran")
            raise _ollama_error(self.base_url, final_exc) from final_exc
        latency = round(time.perf_counter() - t0, 4)

        text = _strip_think(data.get("response", ""))
        tool_calls = data.get("tool_calls") or []
        return LocalAIResponse(
            text=text,
            model=self.model,
            latency_s=latency,
            tool_calls=tool_calls,
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
        )

    def execute_tool_loop(
        self,
        query: str,
        *,
        system: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        max_steps: int = 4,
        max_tokens: int = 2048,
    ) -> LocalAIResponse:
        """Bounded tool-call loop via /api/chat.

        Returns the final assistant text after executing tool calls and feeding
        results back, up to `max_steps`.

        Uses the chat endpoint with the accumulated ``messages`` array — never
        a flattened string — so Ollama's KV cache reuses the message prefix
        across steps. The previous flat-string /api/generate path re-encoded
        the entire history every step (the M1 re-encode tax).
        """
        if not tools:
            return self.generate(query, system=system, max_tokens=max_tokens)

        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": query})

        final_text = ""
        enforcer = StepEnforcer(required_steps=[], terminal_tools=frozenset([t["name"] for t in (tools or [])]))
        for step_idx in range(max_steps):
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
                # The *tool-output* channel: a tool result is the likeliest
                # place for a secret to arrive from outside (a file read, an
                # API body, an exception string) and it goes straight into the
                # next model step. Redacted at the append, so it never reaches
                # the array `chat()` is about to send.
                messages.append({"role": "tool", "content": redact(result)})

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
        # The *prompt* channel, enforced once for every caller and every step:
        # the message array is redacted immediately before it goes on the wire,
        # on a copy so the caller's own list is left alone. This is also what
        # covers the tool loop, which re-enters here with the accumulated
        # messages.
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": redact_obj(messages),
            "stream": False,
            "think": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if tools:
            payload["tools"] = _to_ollama_tools(tools)

        t0 = time.perf_counter()
        with httpx.Client(timeout=settings.request_timeout_s) as client:
            try:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise _ollama_error(self.base_url, exc) from exc
            data = resp.json()
        latency = round(time.perf_counter() - t0, 4)

        msg = data.get("message", {}) or {}
        text = _strip_think(msg.get("content", "") or "")
        tool_calls = msg.get("tool_calls") or []
        return LocalAIResponse(
            text=text,
            model=self.model,
            latency_s=latency,
            tool_calls=tool_calls,
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
        )
