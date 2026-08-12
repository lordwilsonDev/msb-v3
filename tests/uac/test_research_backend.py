"""Tests for the shared Tavily research backend (Stage 0 + flywheel scanner).

The include_domains extension powers the flywheel's arxiv-restricted paper
feed; these tests prove the payload contract without any network call.
"""
from __future__ import annotations

import httpx

from msb_v3.uac.research_backend import TavilyResearchBackend


class _FakeResp:
    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


def _capturing_post(captured: dict):
    def _post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResp(
            {"results": [{"title": "t", "url": "u", "content": "c", "score": 0.5}]}
        )

    return _post


def test_include_domains_flow_into_payload(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(httpx, "post", _capturing_post(captured))
    backend = TavilyResearchBackend(api_key="test-key")

    results = backend.search("q", max_results=2, include_domains=["arxiv.org"])

    assert captured["json"]["include_domains"] == ["arxiv.org"]
    assert captured["json"]["max_results"] == 2
    assert captured["json"]["api_key"] == "test-key"
    assert captured["json"]["search_depth"] == "basic"
    assert len(results) == 1


def test_include_domains_omitted_when_not_requested(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(httpx, "post", _capturing_post(captured))
    backend = TavilyResearchBackend(api_key="test-key")

    backend.search("q")  # Stage 0's existing call: no domain restriction

    assert "include_domains" not in captured["json"]
