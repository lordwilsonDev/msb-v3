"""Pluggable generative brain for the flywheel.

The loop mechanics and brakes are real; the research *quality* is a
pluggable interface. Two chargers:

- StubCharger — deterministic, offline, zero-cost. Produces a UIM in the
  exact shape SovereignResearchAssistant writes ({topic, slug, phase1,
  ok}), seeded from the problem hash so the same problem always yields
  the same artifact (the first turn is reproducible before the real brain
  is attached).
- SovereignCharger — opt-in: runs the real SovereignResearchAssistant
  inversion (a local-LLM call) and returns its UIM file.

The paper scanner is a pluggable PaperScanner with two implementations:

- TavilyScanner — the real feed (Phase 2b): queries Tavily restricted to
  arxiv.org and returns actual papers (title/url/content/score) plus
  next-problem candidates. Missing key or feed outage degrades to an
  honest papers_scanned: 0 note — it never fabricates a scan.
- StubScanner — deterministic offline fallback: reports papers_scanned: 0
  with an explicit note; never fakes a scan.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from msb_v3.uac.research_backend import (
    ResearchBackend,
    ResearchBackendError,
    TavilyResearchBackend,
)

_PREDICTION_POOL = [
    "A 6-month longitudinal measurement shows the expected effect at 2x the control baseline.",
    "A controlled replication on an independent dataset fails to reproduce the headline effect.",
    "Adoption in the field converges on a hybrid that keeps the legacy path for edge cases.",
    "The bottleneck shifts from the studied component to its upstream dependency.",
    "Cost per unit of outcome drops only after the third iteration of the approach.",
    "The naive baseline outperforms the proposed method in low-resource regimes.",
]


class StubCharger:
    """Deterministic offline UIM — same problem -> same artifact."""

    def charge(self, problem: str, slug: str) -> Dict[str, Any]:
        seed = hashlib.sha256(problem.encode()).hexdigest()
        rng = random.Random(seed)
        picks = rng.sample(_PREDICTION_POOL, 3)
        return {
            "topic": problem,
            "slug": slug,
            "phase1": {
                "assumption": f"The dominant claim for '{problem}' is that the current approach is sufficient.",
                "inversion": f"The opposite claim: '{problem}' requires a fundamentally different approach.",
                "predictions": picks,
            },
            "ok": True,
        }


class SovereignCharger:
    """Opt-in real charger: runs the SovereignResearchAssistant inversion
    (local LLM) and returns its UIM artifact."""

    def __init__(self, runtime_root: Optional[Path] = None) -> None:
        self.runtime_root = runtime_root

    def charge(self, problem: str, slug: str) -> Dict[str, Any]:
        from msb_v3.harnesses.research_assistant import SovereignResearchAssistant

        assistant = SovereignResearchAssistant(
            topic=problem, slug=slug, runtime_root=self.runtime_root
        )
        assistant.run_inversion()  # writes {slug}_UIM.json
        uim_path = assistant.runtime_root / f"{assistant.slug}_UIM.json"
        return json.loads(uim_path.read_text())


class PaperScanner(Protocol):
    """scan(problem, uim) -> {papers_scanned, matches, candidates, notes}.

    matches: list of {title, url, content, score}; candidates: next-problem
    statements to surface (paper-derived first, UIM predictions as backup).
    Honest contract: papers_scanned is the real count or 0 with a note —
    never a fabricated number."""

    def scan(self, problem: str, uim: Dict[str, Any]) -> Dict[str, Any]:
        ...


def _clean_title(title: str) -> str:
    """Strip Tavily's arxiv title noise ("[PDF] ", "[2604.17450] ",
    trailing " - arXiv") so surfaced candidates read like problem
    statements, not search results."""
    t = re.sub(r"^\[([0-9.]+|PDF)\]\s*", "", str(title))
    t = re.sub(r"\s*-\s*arXiv$", "", t, flags=re.IGNORECASE)
    return t.strip()


def _merge_candidates(paper_titles: List[str], uim: Dict[str, Any], cap: int = 3) -> List[str]:
    """Paper titles first (the real feed leads), UIM predictions as backup;
    deduped, order-stable, capped."""
    predictions = (uim.get("phase1") or {}).get("predictions", []) or []
    out: List[str] = []
    seen = set()
    for raw in list(paper_titles) + list(predictions):
        cand = str(raw).strip()
        if cand and cand not in seen:
            seen.add(cand)
            out.append(cand)
        if len(out) >= cap:
            break
    return out


class TavilyScanner:
    """Real paper feed (Phase 2b): Tavily web search restricted to arxiv.org.

    Composes the shared TavilyResearchBackend (the repo's one Tavily client,
    also used by Stage 0). A missing key or a feed outage degrades to an
    honest papers_scanned: 0 note instead of raising — the scan is advisory
    (the brakes are the load-bearing gates) and a dead feed must not error
    a turn. Tests inject a fake backend; CI never touches the network.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        backend: Optional[ResearchBackend] = None,
        max_results: int = 5,
        include_domains: Optional[List[str]] = None,
    ) -> None:
        self._api_key = api_key
        self._backend = backend
        self._max_results = max_results
        self._include_domains = list(include_domains or ["arxiv.org"])

    def scan(self, problem: str, uim: Dict[str, Any]) -> Dict[str, Any]:
        query = f"{problem} arxiv paper"
        try:
            backend = self._backend or TavilyResearchBackend(api_key=self._api_key)
            results = backend.search(
                query, max_results=self._max_results, include_domains=self._include_domains
            )
        except ResearchBackendError as exc:
            return {
                "papers_scanned": 0,
                "matches": [],
                "candidates": _merge_candidates([], uim),
                "notes": f"feed unavailable: {exc}",
            }
        matches = [
            {
                "title": _clean_title(r.title),
                "url": r.url,
                "content": (r.content or "")[:300],
                "score": float(r.score),
            }
            for r in results
        ]
        return {
            "papers_scanned": len(matches),
            "matches": matches,
            "candidates": _merge_candidates([str(m["title"]) for m in matches], uim),
            "notes": f"tavily: {len(matches)} paper(s) on '{query}'",
        }


class StubScanner:
    """Honest offline fallback: surfaces problem candidates from the UIM
    itself and reports 0 papers scanned — never fakes a scan."""

    def scan(self, problem: str, uim: Dict[str, Any]) -> Dict[str, Any]:
        predictions = (uim.get("phase1") or {}).get("predictions", [])
        return {
            "papers_scanned": 0,
            "matches": [],
            "candidates": predictions[:3],
            "notes": "scanner stub — offline fallback (set TAVILY_API_KEY for the real feed)",
        }
