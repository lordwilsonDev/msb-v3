"""MoIE controller (spec §3, §24, §25; Phase 3 §31 items 18-24).

    claim → router → experts (each: extract assumptions → invert →
    falsifiable predictions) → evidence merger (fabric recall) →
    contradiction detector → meta-critic → MoIEDecision

``run`` is synchronous and deterministic; every seam (expert registry,
evidence retriever) is injectable so tests pin semantics without touching
real DBs, and a future LLM-backed domain expert slots into the same
interface.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from msb_v3.moie.experts import Expert, ExpertRegistry
from msb_v3.moie.merger import Retriever, retrieve_evidence
from msb_v3.moie.meta_critic import detect_contradictions, synthesize
from msb_v3.moie.models import ExpertReport, MoIEDecision
from msb_v3.moie.router import select_experts

logger = logging.getLogger(__name__)


class MoIEController:
    """The MoIE meta-controller: which experts, how many, in what order,
    with what evidence — then the final decision."""

    def __init__(
        self,
        *,
        registry: Optional[ExpertRegistry] = None,
        retriever: Optional[Retriever] = None,
        tenant: str = "default",
    ) -> None:
        self.registry = registry or ExpertRegistry()
        self.retriever = retriever
        self.tenant = tenant

    def analyze(
        self,
        claim: str,
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> MoIEDecision:
        claim, experts, context, evidence_ids = self._prepare(claim, context)
        if not experts:
            return synthesize(claim, [], [])

        reports: List[ExpertReport] = [self._safe_analyze(e, claim, context) for e in experts]
        return self._finalize(claim, reports, evidence_ids)

    async def aanalyze(
        self,
        claim: str,
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> MoIEDecision:
        """Concurrent ``analyze``: experts run in parallel (thread pool).

        Same output as ``analyze`` (``asyncio.gather`` preserves input order,
        so the report ordering and contradiction tie-breaks are identical);
        used where multiple slow experts — e.g. a diverse LLM reviewer
        panel — must not serialize their latency.
        """
        claim, experts, context, evidence_ids = self._prepare(claim, context)
        if not experts:
            return synthesize(claim, [], [])

        reports: List[ExpertReport] = list(
            await asyncio.gather(*(asyncio.to_thread(self._safe_analyze, e, claim, context) for e in experts))
        )
        return self._finalize(claim, reports, evidence_ids)

    # -- internals ------------------------------------------------------

    def _prepare(
        self,
        claim: str,
        context: Optional[Dict[str, Any]],
    ) -> tuple[str, List[Expert], Dict[str, Any], List[str]]:
        """Shared prologue: normalize claim, select experts, retrieve evidence."""
        claim = (claim or "").strip()
        context = context or {}
        if not claim:
            return claim, [], context, []
        experts = select_experts(self.registry, claim, context)
        if not experts:
            # Can only happen with an empty custom registry — fail honestly.
            return claim, [], context, []
        evidence = retrieve_evidence(claim, self.retriever, tenant=self.tenant)
        evidence_ids = [e["memory_id"] for e in evidence if e.get("memory_id")]
        return claim, experts, context, evidence_ids

    def _safe_analyze(self, expert: Expert, claim: str, context: Dict[str, Any]) -> ExpertReport:
        """One expert's report, fail-closed on any exception (CONCERN, never silent)."""
        try:
            return expert.analyze(claim, context=context)
        except Exception as exc:  # noqa: BLE001 — one broken expert must not sink the analysis
            logger.warning("moie expert %s failed for claim %r: %s", expert.expert_id, claim[:80], exc)
            return ExpertReport(
                expert_id=expert.expert_id,
                expert_name=getattr(expert, "name", expert.expert_id),
                verdict="CONCERN",  # fail-closed on a broken expert
                confidence=0.3,
                summary=f"{expert.expert_id} analysis failed: {type(exc).__name__}",
                risks=[f"{expert.expert_id} could not complete its inversion"],
            )

    def _finalize(
        self,
        claim: str,
        reports: List[ExpertReport],
        evidence_ids: List[str],
    ) -> MoIEDecision:
        """Shared epilogue: attach evidence hits, detect contradictions, synthesize."""
        for report in reports:
            report.evidence_hits = list(evidence_ids)
        contradictions = detect_contradictions(reports)
        return synthesize(claim, reports, contradictions)
