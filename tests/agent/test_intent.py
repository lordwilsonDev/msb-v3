"""Tests for the agent intent interpreter (msb_v3.agent.intent)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.agent.intent import Intent, _extract_json, interpret_intent  # noqa: E402


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "fake"
        self.latency_s = 0.0
        self.tool_calls = []


class _FakeClient:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = []

    def generate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
        self.calls.append({"prompt": prompt, "system": system, "temperature": temperature})
        return _Resp(self._text)


class _BrokenClient:
    def generate(self, *args, **kwargs):
        raise ConnectionError("ollama down")


def test_fallback_when_model_unreachable() -> None:
    intent = interpret_intent("  research the vault  ", client=_BrokenClient())
    assert intent.source == "fallback"
    assert intent.goals == ("research the vault",)  # whitespace trimmed
    assert intent.constraints == ()
    assert intent.permissions == ()
    assert intent.privacy is True


def test_llm_path_parses_structured_intent() -> None:
    client = _FakeClient(
        '{"goals": ["research the topic"], "constraints": ["under 500 words"], '
        '"permissions": ["read_vault", "write_file"], "privacy": true, "domain": "client-brief"}'
    )
    intent = interpret_intent("Handle this: research and write a brief", client=client)
    assert intent.source == "llm"
    assert intent.goals == ("research the topic",)
    assert intent.constraints == ("under 500 words",)
    assert intent.permissions == ("read_vault", "write_file")
    assert intent.privacy is True
    assert intent.domain == "client-brief"
    # The interpreter drives the model deterministically (temperature 0).
    assert client.calls[0]["temperature"] == 0.0
    assert client.calls[0]["system"] is not None


def test_fenced_and_noisy_json_still_parses() -> None:
    client = _FakeClient(
        'Here you go:\n```json\n{"goals": ["a"], "constraints": [], "permissions": [], "privacy": true}\n```\nEnjoy!'
    )
    intent = interpret_intent("x", client=client)
    assert intent.source == "llm"
    assert intent.goals == ("a",)


def test_privacy_false_is_respected() -> None:
    client = _FakeClient('{"goals": ["post publicly"], "constraints": [], "permissions": [], "privacy": false}')
    intent = interpret_intent("post it", client=client)
    assert intent.source == "llm"
    assert intent.privacy is False


def test_garbage_model_output_falls_back() -> None:
    intent = interpret_intent("do the thing", client=_FakeClient("I'm sorry, I can't do that."))
    assert intent.source == "fallback"
    assert intent.goals == ("do the thing",)


def test_goals_required_else_fallback() -> None:
    # A parseable JSON object with no goals is useless — fall back.
    client = _FakeClient('{"goals": [], "constraints": [], "permissions": []}')
    intent = interpret_intent("do the thing", client=client)
    assert intent.source == "fallback"


def test_empty_request_is_fallback_with_no_goals() -> None:
    # No model call is made for an empty request; no metric is incremented.
    client = _FakeClient('{"goals": ["x"]}')
    intent = interpret_intent("   ", client=client)
    assert intent.source == "fallback"
    assert intent.goals == ()
    assert client.calls == []  # early return — the model is never hit


def test_extract_json_rejects_non_object() -> None:
    assert _extract_json("[1, 2, 3]") is None
    assert _extract_json("") is None
    assert _extract_json("no braces here") is None
    assert _extract_json('{"a": 1') is None  # unbalanced trailing brace


def test_extract_json_handles_braces_inside_strings() -> None:
    # Braces inside string values must not corrupt the balanced scan.
    assert _extract_json('{"goals": ["say } here"], "constraints": [], "permissions": []}') == {
        "goals": ["say } here"],
        "constraints": [],
        "permissions": [],
    }
    assert _extract_json('{"a": {"b": 1}}') == {"a": {"b": 1}}  # nested object


def test_privacy_string_false_defaults_to_private() -> None:
    # A non-bool "false" must not flip the routing — unknown means private.
    client = _FakeClient('{"goals": ["g"], "constraints": [], "permissions": [], "privacy": "false"}')
    intent = interpret_intent("x", client=client)
    assert intent.source == "llm"
    assert intent.privacy is True


def test_intent_metrics_move_on_fallback() -> None:
    from prometheus_client.registry import REGISTRY

    before = (
        REGISTRY.get_sample_value(
            "msb_v3_queries_total", {"harness": "agentic", "event": "intent:fallback"}
        )
        or 0.0
    )
    interpret_intent("probe", client=_BrokenClient())
    after = (
        REGISTRY.get_sample_value(
            "msb_v3_queries_total", {"harness": "agentic", "event": "intent:fallback"}
        )
        or 0.0
    )
    assert after == before + 1


def test_as_dict_round_trip() -> None:
    intent = Intent(request="r", goals=("g",), permissions=("write_file",), source="llm")
    d = intent.as_dict()
    assert d["goals"] == ["g"]
    assert d["permissions"] == ["write_file"]
    assert d["source"] == "llm"
