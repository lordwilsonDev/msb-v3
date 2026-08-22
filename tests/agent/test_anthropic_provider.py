"""Anthropic provider — the third harness behind the AgentProvider ABC.

Pins the two halves of the harness bar (same as DeepSeek):

1. ``AnthropicClient`` is a correct Anthropic Messages-API client: it posts
   to ``/messages`` with ``x-api-key`` + ``anthropic-version`` headers, puts
   the system prompt top-level (never a system message), and decodes
   ``tool_use``/``tool_result`` content blocks (not OpenAI tool_calls).
2. ``AnthropicAgentProvider`` routes a governed run through
   ``agent.handle()`` — a BLOCKed request makes zero model calls and still
   emits one evidence receipt; a PASS run emits one receipt with a verified
   run id.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "contracts"))

from _helpers import (  # noqa: E402
    INTENT_WITH_WRITE,
    Audit,
    FakeMoIE,
    FakeProvider,
)

from msb_v3.agent.handle import handle  # noqa: E402
from msb_v3.agent.providers import (  # noqa: E402
    AnthropicAgentProvider,
    ProviderRegistry,
    default_providers,
)
from msb_v3.agent.safety import ActionGate  # noqa: E402
from msb_v3.core.config import settings  # noqa: E402
from msb_v3.local_ai.anthropic import AnthropicClient  # noqa: E402


def _anthropic(content: str, tool_uses: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """Anthropic Messages-API response shape: content blocks + usage."""
    blocks: List[Dict[str, Any]] = []
    if content:
        blocks.append({"type": "text", "text": content})
    for tu in tool_uses or []:
        blocks.append({"type": "tool_use", "id": tu["id"], "name": tu["name"], "input": tu.get("input", {})})
    return {
        "content": blocks,
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }


def _client(handler: Any) -> AnthropicClient:
    return AnthropicClient(
        base_url="https://api.anthropic.com/v1",
        api_key="sk-ant-test",
        model="claude-sonnet-4-5",
        transport=httpx.MockTransport(handler),
    )


def _redirect_audit_log(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(settings, "audit_log_path", str(path))


def _lines(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


# ── client: Anthropic Messages-API shapes ───────────────────────────────────


def test_anthropic_client_chat_posts_messages_shape() -> None:
    captured: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_anthropic("hi"))

    resp = _client(handler).chat([{"role": "user", "content": "hello"}], max_tokens=500)
    assert resp.text == "hi"
    assert resp.prompt_tokens == 7
    assert resp.completion_tokens == 3
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    headers = captured["headers"]
    assert headers["x-api-key"] == "sk-ant-test"
    assert "anthropic-version" in headers  # the Messages-API version header
    body = captured["body"]
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["model"] == "claude-sonnet-4-5"
    assert body["max_tokens"] == 500
    assert "prompt" not in body  # Messages shape, never the flat-string Ollama shape


def test_anthropic_client_system_prompt_is_top_level() -> None:
    captured: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_anthropic("ok"))

    _client(handler).generate("do it", system="You are MSB.")
    body = captured["body"]
    assert body["system"] == "You are MSB."  # top-level field, never a system message
    assert all(m["role"] != "system" for m in body["messages"])


def test_anthropic_client_execute_tool_loop_roundtrips_tool_use() -> None:
    responses = [
        _anthropic("call it", tool_uses=[{"id": "toolu_01", "name": "lookup", "input": {"q": "x"}}]),
        _anthropic("done"),
    ]
    posted: List[Dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200, json=responses.pop(0))

    client = _client(handler)
    calls: List[str] = []

    def lookup(q: str) -> str:
        calls.append(q)
        return f"result:{q}"

    client.register_tool("lookup", lookup)
    resp = client.execute_tool_loop("find", tools=[{"name": "lookup", "description": "x"}], max_steps=3)
    assert resp.text == "done"
    assert calls == ["x"]  # tool_use input dict was dispatched to the registered tool
    assert len(posted) == 2
    # The tool result round-trips as a tool_result block inside a user turn,
    # carrying the tool_use id (Anthropic has no separate tool role).
    last = posted[1]["messages"][-1]
    assert last["role"] == "user"
    assert last["content"][0]["type"] == "tool_result"
    assert last["content"][0]["tool_use_id"] == "toolu_01"
    assert last["content"][0]["content"] == "result:x"


def test_anthropic_client_402_opens_circuit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": {"type": "payment_error"}})

    client = _client(handler)
    with pytest.raises(ConnectionError, match="402"):
        client.chat([{"role": "user", "content": "hi"}])
    # Circuit is now open — a second call short-circuits without hitting the wire.
    with pytest.raises(ConnectionError, match="circuit open"):
        client.chat([{"role": "user", "content": "hi"}])
    # And it is observable for health.
    from msb_v3.local_ai.anthropic import anthropic_circuit_state

    state = anthropic_circuit_state()
    assert state["open"] is True


# ── provider: wiring ────────────────────────────────────────────────────────


def test_anthropic_provider_available_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    provider = AnthropicAgentProvider()
    assert provider.available() is False
    assert "ANTHROPIC_API_KEY" in provider.unavailable_reason()

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    assert provider.available() is True
    assert provider.unavailable_reason() == ""


def test_anthropic_provider_registered() -> None:
    registry = ProviderRegistry(default_providers())
    provider = registry.get("api.anthropic")
    assert provider is not None
    assert isinstance(provider, AnthropicAgentProvider)
    assert provider.spec.kind == "api"


@pytest.mark.asyncio
async def test_anthropic_provider_execute_delegates_to_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(lambda r: httpx.Response(500, json={}))
    seen: Dict[str, Any] = {}

    async def fake_handle(request: str, *, client: Any = None, **kwargs: Any) -> SimpleNamespace:
        seen["request"] = request
        seen["client"] = client
        seen["spine"] = kwargs.get("spine")
        return SimpleNamespace(
            ok=True,
            run_id="run-1",
            verdict="PASS",
            deterministic_hash="abc",
            trace={"outcome": {"ok": True}},
            error=None,
        )

    monkeypatch.setattr("msb_v3.agent.handle.handle", fake_handle)
    result = await AnthropicAgentProvider(client=client).execute("do a thing")

    assert result.ok is True
    assert result.artifacts["run_id"] == "run-1"
    assert result.artifacts["deterministic_hash"] == "abc"
    assert seen["request"] == "do a thing"
    assert seen["client"] is client
    assert seen["spine"] is not None  # the provider wires the evidence spine


# ── provider end-to-end: governed + receipt ─────────────────────────────────


@pytest.mark.asyncio
async def test_anthropic_blocked_run_zero_calls_and_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "audit.jsonl"
    _redirect_audit_log(monkeypatch, log)
    requests: List[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, json={})

    client = _client(handler)
    result = await handle(
        "rm -rf production",
        client=client,
        approve=True,
        provider=FakeProvider(tmp_path),
        gate=ActionGate(audit_chain=Audit()),
        moie=FakeMoIE("BLOCK"),
        spine=None,
    )
    assert result.verdict == "BLOCKED"
    assert result.model_calls == 0
    assert requests == []  # the gate denied before any Anthropic call

    receipts = _lines(log)
    assert len(receipts) == 1
    assert receipts[0]["request_id"] == result.run_id
    assert receipts[0]["authorization_decision"] == "DENY"
    assert receipts[0]["model_calls"] == 0


@pytest.mark.asyncio
async def test_anthropic_pass_run_emits_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "audit.jsonl"
    _redirect_audit_log(monkeypatch, log)
    responses = [_anthropic(INTENT_WITH_WRITE), _anthropic("garbage")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    client = _client(handler)
    result = await handle(
        "research the vault and write a client brief",
        client=client,
        approve=True,
        provider=FakeProvider(tmp_path),
        gate=ActionGate(audit_chain=Audit()),
        moie=FakeMoIE("APPROVE"),
        spine=None,
    )
    assert result.verdict == "PASS"
    assert result.model_calls >= 2  # intent + plan via the Anthropic client

    receipts = _lines(log)
    assert len(receipts) == 1
    assert receipts[0]["request_id"] == result.run_id
    assert receipts[0]["execution_result"]["verdict"] == "PASS"
