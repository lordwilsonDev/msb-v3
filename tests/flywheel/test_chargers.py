"""Charger tests — the stub brain is deterministic and UIM-compatible;
the real Tavily scanner is feed-honest and hermetic (fake backends only)."""

from __future__ import annotations

from typing import List

from msb_v3.flywheel.chargers import StubCharger, StubScanner, TavilyScanner, _merge_candidates
from msb_v3.uac.research_backend import ResearchBackendError, SearchResult


def test_stub_charger_uim_shape() -> None:
    uim = StubCharger().charge("Sovereign memory consolidation", "sovereign-memory")
    assert uim["topic"] == "Sovereign memory consolidation"
    assert uim["slug"] == "sovereign-memory"
    assert uim["ok"] is True
    phase = uim["phase1"]
    assert phase["assumption"]
    assert phase["inversion"]
    assert len(phase["predictions"]) == 3


def test_stub_charger_deterministic() -> None:
    charger = StubCharger()
    a = charger.charge("Same problem statement", "same-slug")
    b = charger.charge("Same problem statement", "same-slug")
    assert a == b


def test_stub_charger_differs_across_problems() -> None:
    charger = StubCharger()
    a = charger.charge("Problem A", "a")
    b = charger.charge("Problem B", "b")
    assert a["phase1"]["inversion"] != b["phase1"]["inversion"]


def test_stub_scanner_is_honest() -> None:
    uim = StubCharger().charge("Scan me", "scan-me")
    result = StubScanner().scan("Scan me", uim)
    assert result["papers_scanned"] == 0  # never fakes a scan
    assert "stub" in result["notes"]
    assert len(result["candidates"]) >= 1


class _FakeBackend:
    """Records the search call and returns canned results — CI stays offline."""

    def __init__(self, results=None, exc: Exception | None = None) -> None:
        self.results = results or []
        self.exc = exc
        self.calls: List[dict] = []

    def search(self, query: str, max_results: int = 5, include_domains=None):
        self.calls.append(
            {"query": query, "max_results": max_results, "include_domains": include_domains}
        )
        if self.exc is not None:
            raise self.exc
        return self.results[:max_results]


_PAPERS = [
    SearchResult(
        title="A Survey of Sovereign Mesh Networks",
        url="https://arxiv.org/abs/2506.00001",
        content="A comprehensive survey of peer-to-peer mesh architectures.",
        score=0.91,
    ),
    SearchResult(
        title="Local-First Agent Architectures",
        url="https://arxiv.org/abs/2506.00002",
        content="Agents that operate without a central coordinator.",
        score=0.87,
    ),
]


def test_tavily_scanner_returns_real_papers() -> None:
    backend = _FakeBackend(results=_PAPERS)
    scanner = TavilyScanner(api_key="test-key", backend=backend)
    uim = StubCharger().charge("Mesh for local-first agents", "mesh")

    result = scanner.scan("Mesh for local-first agents", uim)

    assert result["papers_scanned"] == 2
    assert result["matches"][0]["title"] == _PAPERS[0].title
    assert result["matches"][0]["url"].startswith("https://arxiv.org/")
    assert "tavily" in result["notes"]
    # the real feed leads the candidates: the top paper title is candidate #1
    assert result["candidates"][0] == _PAPERS[0].title


def test_tavily_scanner_strips_arxiv_title_noise() -> None:
    backend = _FakeBackend(
        results=[
            SearchResult(
                title="[2604.17450] Compiling Deterministic Structure into SLM Harnesses - arXiv",
                url="https://arxiv.org/abs/2604.17450",
                content="",
                score=0.9,
            ),
            SearchResult(
                title="[PDF] A Verifiable Learning Substrate with Ledger-Attested Feedback",
                url="https://arxiv.org/pdf/2601.00816",
                content="",
                score=0.8,
            ),
        ]
    )
    scanner = TavilyScanner(api_key="test-key", backend=backend)

    result = scanner.scan("Deterministic harnesses", {})

    # candidates must read like problem statements, not search results
    assert result["matches"][0]["title"] == "Compiling Deterministic Structure into SLM Harnesses"
    assert result["matches"][1]["title"] == "A Verifiable Learning Substrate with Ledger-Attested Feedback"
    assert result["candidates"][0] == "Compiling Deterministic Structure into SLM Harnesses"


def test_tavily_scanner_restricts_to_arxiv() -> None:
    backend = _FakeBackend(results=_PAPERS)
    scanner = TavilyScanner(api_key="test-key", backend=backend)
    scanner.scan("Anything", {})
    assert backend.calls[0]["include_domains"] == ["arxiv.org"]
    assert "arxiv paper" in backend.calls[0]["query"]


def test_tavily_scanner_degrades_on_feed_error_never_fabricates() -> None:
    backend = _FakeBackend(exc=ResearchBackendError("simulated outage"))
    scanner = TavilyScanner(api_key="test-key", backend=backend)
    uim = StubCharger().charge("Topic", "topic")

    result = scanner.scan("Topic", uim)

    assert result["papers_scanned"] == 0  # honest: nothing was scanned
    assert result["matches"] == []
    assert result["notes"].startswith("feed unavailable")
    # candidates fall back to the UIM predictions — the loop still surfaces
    assert result["candidates"] == (uim.get("phase1") or {}).get("predictions", [])[:3]


def test_merge_candidates_papers_lead_dedupe_cap() -> None:
    uim = {"phase1": {"predictions": ["Pred A", "Pred B", "Pred C"]}}
    merged = _merge_candidates(["Paper X", "Pred A", "Paper X", "Paper Y"], uim, cap=3)
    # order-stable across the merged stream, papers first, duplicates dropped,
    # capped at 3
    assert merged == ["Paper X", "Pred A", "Paper Y"]
    assert len(merged) == 3
