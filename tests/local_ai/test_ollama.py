"""Tests for the Ollama client — incl. the /think leak fix (chaos-test finding).

The qwen3 template appends the "/think" control token to the last user message
when thinking-mode is left at its default, so the token leaks into the prompt
the model reads as text. The client must (1) pin think=False on both endpoints
and (2) strip any <think>...</think> blocks from output.
"""

from __future__ import annotations

from typing import Any, Dict, List

from msb_v3.local_ai.ollama import LocalAIClient, _strip_think


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
        self.payloads.append(json)
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
    post = _FakePost(
        [
            {
                "response": "I need to look that up.",
                "tool_calls": [
                    {"function": {"name": "nope", "arguments": {}}}
                ],
            },
            {"response": "<think>scratch</think>answer is 42", "tool_calls": None},
        ]
    )
    _patch_client(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    client.register_tool("nope", lambda **kw: "unused")
    resp = client.execute_tool_loop("what", tools=[{"name": "nope", "description": "x"}], max_steps=3)

    assert resp.text == "answer is 42"


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
