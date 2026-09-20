"""Tests for the Ollama client — incl. the /think leak fix (chaos-test finding).

The qwen3 template appends the "/think" control token to the last user message
when thinking-mode is left at its default, so the token leaks into the prompt
the model reads as text. The client must (1) pin think=False on both endpoints
and (2) strip any <think>...</think> blocks from output.

The tool loop uses /api/chat with the accumulated messages array — never a
flattened string to /api/generate — so Ollama's KV cache reuses the message
prefix across steps instead of re-encoding the whole history (the M1 re-encode
tax).

The tool definitions the repo passes down are FLAT (``ToolSpec`` /
``ToolDef.as_model_schema``); Ollama only honours the nested ``function`` form.
The client converts at the wire boundary. Measured 2026-09-20 against Ollama
0.33.3: with a flat entry the model emitted no ``tool_calls`` at all and
refused in prose, while the identical prompt with the nested entry called the
tool. Nothing downstream could tell — the request still returned 200 — so this
is pinned here rather than left to the live path.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List

import httpx
import pytest

from msb_v3.local_ai.ollama import LocalAIClient, OllamaTimeout, _strip_think


class _FakeResponse:
    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Dict[str, Any]:
        return self._data


class _FakePost:
    """Records every payload sent, returns a canned JSON body per call."""

    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        self.responses = responses
        self.payloads: List[Dict[str, Any]] = []

    def __call__(self, url: str, json: Dict[str, Any], **kwargs: Any) -> _FakeResponse:
        # Deep-copy: the caller mutates the messages list in place across
        # steps, so a live reference would let later steps rewrite an earlier
        # recorded payload. Snapshot what was actually sent.
        self.payloads.append(copy.deepcopy(json))
        body = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return _FakeResponse(body)


class _FakeClient:
    def __init__(self, post: _FakePost) -> None:
        self._post = post

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def post(self, url: str, json: Dict[str, Any], **kwargs: Any) -> _FakeResponse:
        return self._post(url, json, **kwargs)


def _patch_client(monkeypatch, post: _FakePost) -> None:
    monkeypatch.setattr(
        "msb_v3.local_ai.ollama.httpx.Client",
        lambda timeout=None: _FakeClient(post),
    )


def test_strip_think_removes_blocks():
    assert _strip_think("<think>reasoning</think>the answer") == "the answer"
    assert _strip_think("plain answer") == "plain answer"
    assert _strip_think("before <think>one</think> middle <think>two</think> after") == "before  middle  after"
    assert _strip_think("<think></think>") == ""


def test_generate_strips_think_and_pins_flag(monkeypatch):
    post = _FakePost([{"response": "<think>quiet reasoning</think>final output", "tool_calls": None}])
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    resp = client.generate("Say A", max_tokens=64)

    assert resp.text == "final output"
    assert post.payloads[0]["think"] is False
    assert post.payloads[0]["prompt"] == "Say A"


def test_chat_strips_think_and_pins_flag(monkeypatch):
    post = _FakePost([{"message": {"content": "<think></think>hello", "tool_calls": None}}])
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    resp = client.chat([{"role": "user", "content": "hi"}])

    assert resp.text == "hello"
    assert post.payloads[0]["think"] is False


def test_tool_loop_output_is_think_stripped(monkeypatch):
    # First response asks for a tool; second returns think-wrapped final text.
    # /api/chat shape: content + tool_calls live under the message key.
    post = _FakePost(
        [
            {
                "message": {
                    "content": "I need to look that up.",
                    "tool_calls": [
                        {"function": {"name": "nope", "arguments": {}}}
                    ],
                }
            },
            {
                "message": {"content": "<think>scratch</think>answer is 42", "tool_calls": None}
            },
        ]
    )
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    client.register_tool("nope", lambda **kw: "unused")
    resp = client.execute_tool_loop("what", tools=[{"name": "nope", "description": "x"}], max_steps=3)

    assert resp.text == "answer is 42"


def test_tool_loop_uses_chat_endpoint_and_messages(monkeypatch):
    """The loop must send the accumulated messages array to /api/chat (never
    a flattened string to /api/generate), so Ollama's KV cache reuses the
    message prefix across steps instead of re-encoding the whole history."""
    post = _FakePost(
        [
            {"message": {"content": "call the tool", "tool_calls": [{"function": {"name": "nope", "arguments": {}}}]}},
            {"message": {"content": "done", "tool_calls": None}},
        ]
    )
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    client.register_tool("nope", lambda **kw: "unused")
    resp = client.execute_tool_loop("what", tools=[{"name": "nope", "description": "x"}], max_steps=3)

    assert resp.text == "done"
    # Every payload is a chat payload: it carries a messages array, never a
    # flat prompt string.
    for payload in post.payloads:
        assert "messages" in payload
        assert "prompt" not in payload
    assert post.payloads[0]["messages"][-1]["role"] == "user"
    assert post.payloads[1]["messages"][-1]["role"] == "tool"
    # The message prefix is preserved between steps (KV-cache reuse): call 1's
    # messages are a strict prefix of call 2's (user -> user, assistant, tool).
    assert post.payloads[0]["messages"] == post.payloads[1]["messages"][: len(post.payloads[0]["messages"])]


def test_chaos_case_no_think_leak_in_prompt(monkeypatch):
    """The exact chaos finding: 'Say A' must not carry a '/think' token into
    the prompt, and the pinned flag must be sent to the server."""
    post = _FakePost([{"response": "A", "tool_calls": None}])
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    resp = client.generate("Say A", max_tokens=16)

    assert resp.text == "A"
    assert "/think" not in post.payloads[0]["prompt"]
    assert post.payloads[0].get("think") is False


# --- tool wire shape: flat contract in, Ollama's nested shape out -----------


def test_chat_wraps_flat_tool_specs_for_the_wire(monkeypatch):
    """A flat entry must reach the wire nested. This is the regression that
    silently disabled model tool calling: a flat entry is accepted by the
    request and ignored by the model, so nothing upstream can detect it."""
    post = _FakePost([{"message": {"content": "hi", "tool_calls": None}}])
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    client.chat(
        [{"role": "user", "content": "hi"}],
        tools=[
            {
                "type": "function",
                "name": "vault_read",
                "description": "read a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            }
        ],
    )

    sent = post.payloads[0]["tools"][0]
    assert sent == {
        "type": "function",
        "function": {
            "name": "vault_read",
            "description": "read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }
    # No flat leakage: the tool id must not also sit at the top level, or a
    # future reader would think the flat form is accepted here.
    assert "name" not in sent


def test_chat_leaves_already_nested_tool_specs_alone(monkeypatch):
    """A caller holding the OpenAI shape is not double-wrapped."""
    post = _FakePost([{"message": {"content": "hi", "tool_calls": None}}])
    _patch_client(monkeypatch, post)

    nested = {"type": "function", "function": {"name": "vault_read", "parameters": {}}}
    client = LocalAIClient(base_url="http://fake:11434")
    client.chat([{"role": "user", "content": "hi"}], tools=[nested])

    assert post.payloads[0]["tools"] == [nested]
    assert "function" not in post.payloads[0]["tools"][0]["function"]


def test_generate_also_wraps_flat_tool_specs(monkeypatch):
    """Both payload sites convert — /api/generate takes tools too."""
    post = _FakePost([{"response": "ok", "tool_calls": None}])
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    client.generate("hi", tools=[{"name": "vault_read"}])

    assert post.payloads[0]["tools"] == [{"type": "function", "function": {"name": "vault_read"}}]


# --- a timeout is not "unreachable" ------------------------------------------
#
# Measured 2026-09-20 (JOB-022): /chat returned chat:degraded with
# "ConnectionError: ollama unreachable" while Ollama was up and answering. The
# real failure was a ReadTimeout, and the real cause was arithmetic — a
# num_predict budget of 2048 against ~18.5 tok/s needs up to 111s, and the
# default request_timeout_s is 60s. Any tool result substantial enough to
# summarise outran the budget, so the label sent the reader to the network for
# what was a configuration mismatch.


class _RaisingPost:
    """A post() that always raises, counting attempts."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    def __call__(self, url: str, json: Dict[str, Any], **kwargs: Any) -> Any:
        self.calls += 1
        raise self.exc


class _ThenRaisingPost:
    """Returns one canned body, then raises on every later call."""

    def __init__(self, body: Dict[str, Any], exc: Exception) -> None:
        self.body = body
        self.exc = exc
        self.calls = 0

    def __call__(self, url: str, json: Dict[str, Any], **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            return _FakeResponse(self.body)
        raise self.exc


def test_read_timeout_is_reported_as_a_timeout_not_as_unreachable(monkeypatch):
    post = _RaisingPost(httpx.ReadTimeout("timed out"))
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    with pytest.raises(OllamaTimeout) as err:
        client.chat([{"role": "user", "content": "hi"}])

    message = str(err.value)
    assert "timed out after" in message
    assert "unreachable" not in message, "a reachable-but-slow server must not be called unreachable"
    # The handling must not change: callers branch on ConnectionError to mean
    # "the brain is unavailable" and api/automation.py maps that to a 503.
    assert isinstance(err.value, ConnectionError)


def test_a_refused_connection_is_still_reported_as_unreachable(monkeypatch):
    """The new type must not swallow the case its wording was written for."""
    post = _RaisingPost(httpx.ConnectError("Connection refused"))
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    with pytest.raises(ConnectionError) as err:
        client.chat([{"role": "user", "content": "hi"}])

    assert not isinstance(err.value, OllamaTimeout)
    assert "unreachable" in str(err.value)


def test_the_timeout_message_still_classifies_as_transient_for_recovery(monkeypatch):
    """agent/verify.py classifies failures by substring; a reworded message
    would silently drop recoverable timeouts into 'unknown -> human review'."""
    from msb_v3.agent.verify import _TRANSIENT

    post = _RaisingPost(httpx.ReadTimeout("timed out"))
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    with pytest.raises(OllamaTimeout) as err:
        client.chat([{"role": "user", "content": "hi"}])

    lowered = str(err.value).lower()
    assert any(token in lowered for token in _TRANSIENT), lowered


def test_a_timed_out_generation_is_not_retried(monkeypatch):
    """Retrying a generation that outran its budget spends another full budget
    of silence before reporting the same thing — 3 x 60s at the default."""
    post = _RaisingPost(httpx.ReadTimeout("timed out"))
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    with pytest.raises(OllamaTimeout):
        client.generate("hi")

    assert post.calls == 1, "a timeout must be reported, not re-attempted"


def test_a_refused_connection_is_still_retried(monkeypatch):
    """The retry loop keeps doing its actual job: transient refusals."""
    post = _RaisingPost(httpx.ConnectError("Connection refused"))
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    with pytest.raises(ConnectionError):
        client.generate("hi")

    assert post.calls == 3


def test_a_tool_that_ran_does_not_disguise_the_summarising_call_timing_out(monkeypatch):
    """The exact live failure. Iteration 1 calls the tool — so the governed
    identity-shadow record IS written, evidence intact — and iteration 2, which
    feeds a large result back, times out. The caller must be told a timeout
    happened on a model that was reachable throughout."""
    post = _ThenRaisingPost(
        {"message": {"content": "calling", "tool_calls": [{"function": {"name": "vault_lint", "arguments": {}}}]}},
        httpx.ReadTimeout("timed out"),
    )
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    ran: List[str] = []
    client.register_tool("vault_lint", lambda **kw: ran.append("yes") or "a very large lint report")

    with pytest.raises(OllamaTimeout) as err:
        client.execute_tool_loop("lint the vault", tools=[{"name": "vault_lint", "description": "x"}])

    assert ran == ["yes"], "the tool ran, so the failure is on the summarising call"
    assert post.calls == 2, "no third attempt"
    assert "timed out after" in str(err.value)


def test_tool_loop_advertises_nested_tools_and_still_calls_flat_registered_ones(monkeypatch):
    """The full contract in one test: tools are advertised to the model in the
    nested shape, while registration stays keyed on the flat tool id — the two
    shapes must not be unified, because ``register_governed_tools`` needs the
    flat one and Ollama needs the nested one."""
    post = _FakePost(
        [
            {
                "message": {
                    "content": "calling",
                    "tool_calls": [{"function": {"name": "vault_read", "arguments": {"path": "x.md"}}}],
                }
            },
            {"message": {"content": "done", "tool_calls": None}},
        ]
    )
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    seen: Dict[str, Any] = {}

    def _registered(**kwargs: Any) -> str:
        seen.update(kwargs)
        return "file contents"

    client.register_tool("vault_read", _registered)
    resp = client.execute_tool_loop(
        "read x.md",
        tools=[{"name": "vault_read", "description": "read a file"}],
        max_steps=3,
    )

    assert resp.text == "done"
    assert seen == {"path": "x.md"}, "the flat-registered tool must still run"
    assert len(post.payloads) == 2, "two model steps expected"
    for payload in post.payloads:
        assert payload["tools"][0]["function"]["name"] == "vault_read"