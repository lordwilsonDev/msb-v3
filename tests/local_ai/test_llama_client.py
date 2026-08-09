"""Tests for llama.cpp client."""

from __future__ import annotations

from msb_v3.local_ai.llama_client import LlamaCPPClient, LocalAIResponse


def test_response_shape():
    r = LocalAIResponse(text="hi", model="gemma", latency_s=0.1)
    assert r.text == "hi"
    assert r.model == "gemma"
    assert r.latency_s == 0.1
    assert r.tool_calls == []


def test_client_defaults():
    c = LlamaCPPClient()
    assert "8080" in c.base_url
    assert "gemma-4-12b-it" in c.model


def test_run_tool_unknown():
    c = LlamaCPPClient()
    out = c.run_tool("nope", {})
    assert "unknown tool" in out
