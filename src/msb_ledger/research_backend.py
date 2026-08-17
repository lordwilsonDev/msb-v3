"""Pluggable research backend for UAC Stage 0.

2026-08-02 decision: Knowledge Acquisition Engine's "never guess" rule can't
be honestly satisfied by the local LLM alone (see harnesses/research_assistant.py —
it generates from model training knowledge with no external grounding, and its
evidence-grounding hook only hashes locally-supplied files, it doesn't search
anything). This module wires in a real external search API instead, per an
explicit choice to prioritize evidence-backed accuracy over pure local-first
operation for this specific stage.

TavilyResearchBackend requires a real TAVILY_API_KEY (see msb_v3.core.config) —
configured in .env, consumed by Stage 0 and the flywheel's TavilyScanner
(Phase 2b). Without a key, it fails loudly (raises) rather than silently
returning empty/fake results, so a misconfigured Stage 0 run is obviously
broken, not silently wrong. (The flywheel scanner degrades that raise into
an honest 0-papers note instead — the scan is advisory there, not
load-bearing.)
NullResearchBackend is for tests only, returns fixed canned results, never
used as a production default.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol

import httpx

from msb_ledger.config import settings

_TAVILY_URL = "https://api.tavily.com/search"


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    score: float = 0.0


class ResearchBackend(Protocol):
    def search(
        self, query: str, max_results: int = 5, include_domains: Optional[List[str]] = None
    ) -> List[SearchResult]:
        ...


class ResearchBackendError(RuntimeError):
    """Raised on missing config or a failed live search — never swallowed into
    an empty result, since an empty result would look identical to 'searched
    and found nothing' rather than 'couldn't search at all'."""


class TavilyResearchBackend:
    def __init__(self, api_key: Optional[str] = None, timeout_s: float = 20.0) -> None:
        self.api_key = api_key or settings.tavily_api_key
        self.timeout_s = timeout_s
        if not self.api_key:
            raise ResearchBackendError(
                "TAVILY_API_KEY is not configured. Set it in the environment or "
                "msb-v3's .env before running Stage 0 against a real profession — "
                "Stage 0 will not fabricate research results without it."
            )

    def search(
        self,
        query: str,
        max_results: int = 5,
        include_domains: Optional[List[str]] = None,
    ) -> List[SearchResult]:
        payload: dict = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        }
        if include_domains:
            payload["include_domains"] = include_domains
        try:
            resp = httpx.post(_TAVILY_URL, json=payload, timeout=self.timeout_s)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise ResearchBackendError(f"Tavily search failed for query {query!r}: {exc}") from exc
        results = []
        for item in data.get("results", []):
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    content=item.get("content", ""),
                    score=float(item.get("score", 0.0)),
                )
            )
        return results


class NullResearchBackend:
    """Test-only fixed backend — never a production default. Returns whatever
    canned results were configured, regardless of query, so tests are
    deterministic and require no network access."""

    def __init__(self, canned_results: Optional[List[SearchResult]] = None) -> None:
        self.canned_results = canned_results or []
        self.queries_received: List[str] = []

    def search(
        self,
        query: str,
        max_results: int = 5,
        include_domains: Optional[List[str]] = None,
    ) -> List[SearchResult]:
        self.queries_received.append(query)
        return self.canned_results[:max_results]
