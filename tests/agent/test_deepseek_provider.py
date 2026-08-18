"""DeepSeek provider — the first frontier harness behind the AgentProvider ABC.

Pins the two halves of the phase-1 close:

1. ``DeepSeekClient`` is a correct OpenAI-compatible client: it posts the
   messages array to /chat/completions (never the flat-string Ollama shape)
   and decodes OpenAI's JSON-string tool arguments.
2. ``DeepSeekAgentProvider`` routes a governed run through ``agent.handle()``
   — a BLOCKed request makes zero model calls and still emits one evidence
   receipt; a PASS run emits one receipt with a verified run id.
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
    DeepSeekAgentProvider,
    ProviderRegistry,
    default_providers,
)
from msb_v3.agent.safety import ActionGate  # noqa: E402
from msb_v3.core.config import settings  # noqa: E402
from msb_v3.local_ai.deepseek import DeepSeekClient  # noqa: E402


def _openai(content: str, tool_calls: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    return {
        "choices": [{"message": {"content": content, "tool_calls": tool_calls}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }


def _client(handler: Any) -> DeepSeekClient:
    return DeepSeekClient(
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test",
        model="deepseek-chat",
        transport=httpx.MockTransport(handler),
    )


def _redirect_audit_log(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(settings, "audit_log_path", str(path))


def _lines(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


# ── client: OpenAI-compatible shapes ────────────────────────────────────────


def test_deepseek_client_chat_posts_openai_shape() -> None:
    captured: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_openai("hi"))

    resp = _client(handler).chat([{"role": "user", "content": "hello"}], max_tokens=500)
    assert resp.text == "hi"
    assert resp.prompt_tokens == 7
    assert resp.completion_tokens == 3
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    body = captured["body"]
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["model"] == "deepseek-chat"
    assert body["max_tokens"] == 500
    assert "prompt" not in body  # OpenAI shape, never the flat-string Ollama shape


def test_deepseek_client_execute_tool_loop_parses_string_arguments() -> None:
    responses = [
        _openai("call it", tool_calls=[{"function": {"name": "lookup", "arguments": '{"q": "x"}'}}]),
        _openai("done", tool_calls=None),
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
    assert calls == ["x"]  # JSON-string arguments were decoded into a dict
    assert len(posted) == 2
    assert posted[1]["messages"][-1]["role"] == "tool"
    assert posted[1]["messages"][-1]["content"] == "result:x"


# ── provider: wiring ────────────────────────────────────────────────────────


def test_deepseek_provider_available_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    provider = DeepSeekAgentProvider()
    assert provider.available() is False
    assert "DEEPSEEK_API_KEY" in provider.unavailable_reason()

    monkeypatch.setattr(settings, "deepseek_api_key", "sk-test")
    assert provider.available() is True
    assert provider.unavailable_reason() == ""


def test_deepseek_provider_registered() -> None:
    registry = ProviderRegistry(default_providers())
    provider = registry.get("api.deepseek")
    assert provider is not None
    assert isinstance(provider, DeepSeekAgentProvider)
    assert provider.spec.kind == "api"


@pytest.mark.asyncio
async def test_deepseek_provider_execute_delegates_to_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(lambda r: httpx.Response(500, json={}))
    seen: Dict[str, Any] = {}

    async def fake_handle(request: str, *, client: Any = None, **kwargs: Any) -> SimpleNamespace:
        seen["request"] = request
        seen["client"] = client
        return SimpleNamespace(
            ok=True,
            run_id="run-1",
            verdict="PASS",
            deterministic_hash="abc",
            trace={"outcome": {"ok": True}},
            error=None,
        )

    monkeypatch.setattr("msb_v3.agent.handle.handle", fake_handle)
    result = await DeepSeekAgentProvider(client=client).execute("do a thing")

    assert result.ok is True
    assert result.artifacts["run_id"] == "run-1"
    assert result.artifacts["deterministic_hash"] == "abc"
    assert seen["request"] == "do a thing"
    assert seen["client"] is client


# ── provider end-to-end: governed + receipt ─────────────────────────────────


@pytest.mark.asyncio
async def test_deepseek_blocked_run_zero_calls_and_receipt(
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
    assert requests == []  # the gate denied before any DeepSeek call

    receipts = _lines(log)
    assert len(receipts) == 1
    assert receipts[0]["request_id"] == result.run_id
    assert receipts[0]["authorization_decision"] == "DENY"
    assert receipts[0]["model_calls"] == 0


@pytest.mark.asyncio
async def test_deepseek_pass_run_emits_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "audit.jsonl"
    _redirect_audit_log(monkeypatch, log)
    responses = [_openai(INTENT_WITH_WRITE), _openai("garbage")]

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
    assert result.model_calls >= 2  # intent + plan via the DeepSeek client

    receipts = _lines(log)
    assert len(receipts) == 1
    assert receipts[0]["request_id"] == result.run_id
    assert receipts[0]["execution_result"]["verdict"] == "PASS"
