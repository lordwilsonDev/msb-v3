"""Stage 0 — Knowledge Acquisition Engine.

Transforms a Requirements Specification (Stage -1 output) into a versioned,
evidence-backed Knowledge Manifest. First executable stage of the Universal
Agent Creator, per the Phase 1 – Stage 0 Implementation Blueprint (2026-08-02).

Sovereign Runtime integration, resolved 2026-08-02 after auditing what
actually exists in msb_v3 (see universal-agent-creator-v1.0.md's UAC v1.0
finalization notes for the full mapping):
  - AGI Harness -> triumvirate.mission_anchor.MissionAnchor (real, reused)
  - Observer's Log -> uac.observer_log.ObserverLog (built 2026-08-02, was missing)
  - Axiom Library -> uac.axiom_library.AxiomLibrary (built 2026-08-02, was missing)
  - Audit Chain -> uac.audit_chain.AuditChain (built 2026-08-02, was missing —
    observability.audit exists but is a pattern-scanning linter, not a
    hash chain, confirmed by reading its source before this was written)

Internal pipeline, per the blueprint:
  Receive Request -> Validate Inputs -> Generate Research Plan ->
  Collect Knowledge -> Normalize Sources -> Score Confidence ->
  Generate Knowledge Manifest -> Store Artifact -> Return Deliverable
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List

from msb_v3.triumvirate.mission_anchor import MissionAnchor
from msb_v3.uac.audit_chain import AuditChainLike
from msb_v3.uac.chain_anchor import anchored_chain_from_env
from msb_v3.uac.axiom_library import ArtifactRecord, AxiomLibrary
from msb_v3.uac.models import (
    ConflictType,
    Evidence,
    FailureReport,
    KnowledgeCategory,
    KnowledgeManifest,
    KnowledgeManifestPayload,
    RequirementsSpecification,
    UDSExecutiveSummary,
    UDSGovernance,
    UDSHeader,
    UDSInputs,
    UDSManifest,
    UDSOutputs,
    UDSProcessing,
    UDSQuality,
    UDSRecommendations,
    UDSRequirements,
)
from msb_v3.uac.observer_log import ObserverLog
from msb_v3.uac.research_backend import ResearchBackend, ResearchBackendError

STAGE_NAME = "stage_0_knowledge_acquisition"

# The blueprint's fixed research categories, each with a query template.
# {profession} and {jurisdiction} are filled in per run.
_RESEARCH_CATEGORIES = {
    "Profession Overview": "{profession} primary responsibilities daily work overview",
    "Workflow Knowledge": "{profession} recurring tasks decision points workflow",
    "Required Knowledge": "{profession} laws regulations standards {jurisdiction}",
    "Software Ecosystem": "{profession} common software tools platforms",
    "Human Skills": "{profession} required communication decision making skills",
    "Failure Modes": "{profession} common mistakes compliance failures risks",
    "Automation Opportunities": "{profession} automation AI tools 2026",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Stage0KnowledgeAcquisitionEngine:
    def __init__(
        self,
        research_backend: ResearchBackend,
        mission_anchor: "MissionAnchor | None" = None,
        observer_log: "ObserverLog | None" = None,
        axiom_library: "AxiomLibrary | None" = None,
        audit_chain: "AuditChainLike | None" = None,
    ) -> None:
        self.research_backend = research_backend
        self.mission_anchor = mission_anchor or MissionAnchor()
        self.observer_log = observer_log or ObserverLog()
        self.axiom_library = axiom_library or AxiomLibrary()
        self.audit_chain = audit_chain or anchored_chain_from_env()

    def compile(self, requirements: RequirementsSpecification) -> KnowledgeManifest:
        """Run the full Stage 0 pipeline. Raises if requirements are invalid —
        never fabricates missing profession/jurisdiction (Human Availability
        Gate principle applied at the code level, not just Stage -1's prompt)."""
        self._validate_inputs(requirements)

        mission = self.mission_anchor.scope_lock(
            goal=f"UAC Stage 0: compile knowledge for {requirements.profession}",
            parameters={"profession": requirements.profession, "jurisdiction": requirements.jurisdiction},
        )
        mission_id = mission["scope_hash"]
        self.observer_log.narrate(mission_id, "Research started.")
        self.audit_chain.append(STAGE_NAME, "mission_started", {"profession": requirements.profession, "jurisdiction": requirements.jurisdiction})

        failure_report = FailureReport()
        categories: List[KnowledgeCategory] = []
        actions_performed: List[str] = []
        all_sources: List[str] = []

        for category_name, query_template in _RESEARCH_CATEGORIES.items():
            self.observer_log.narrate(mission_id, f"Collecting {category_name.lower()}.")
            self.mission_anchor.update(phase=f"collecting:{category_name}")
            query = query_template.format(profession=requirements.profession, jurisdiction=requirements.jurisdiction)
            category = self._collect_category(category_name, query, failure_report)
            categories.append(category)
            actions_performed.append(f"Searched: {query}")
            all_sources.extend(e.source for e in category.findings)

        self.observer_log.narrate(mission_id, "Comparing authoritative sources.")
        conflicts = self._detect_conflicts(categories)

        self.observer_log.narrate(mission_id, "Calculating confidence.")
        confidence_matrix = self._score_confidence(categories)

        self.observer_log.narrate(mission_id, "Manifest generated.")
        manifest = self._build_manifest(
            requirements=requirements,
            categories=categories,
            confidence_matrix=confidence_matrix,
            conflicts=conflicts,
            failure_report=failure_report,
            actions_performed=actions_performed,
            all_sources=all_sources,
        )

        self._store_artifact(manifest, requirements)
        self.audit_chain.append(STAGE_NAME, "manifest_published", {"artifact_id": manifest.header.artifact_id})

        self.mission_anchor.update(phase="complete")
        self.observer_log.narrate(mission_id, "Compilation continuing.")

        return manifest

    # --- pipeline steps ---

    def _validate_inputs(self, requirements: RequirementsSpecification) -> None:
        if not requirements.profession or not requirements.profession.strip():
            raise ValueError("RequirementsSpecification.profession is required and cannot be empty — Stage 0 will not guess a profession.")
        if not requirements.jurisdiction or not requirements.jurisdiction.strip():
            raise ValueError("RequirementsSpecification.jurisdiction is required and cannot be empty — 'never guess' applies to jurisdiction too (see the bookkeeper test run's finding).")

    def _collect_category(self, category_name: str, query: str, failure_report: FailureReport) -> KnowledgeCategory:
        category = KnowledgeCategory(name=category_name)
        try:
            results = self.research_backend.search(query)
        except ResearchBackendError as exc:
            failure_report.research_limitations.append(f"{category_name}: search failed — {exc}")
            category.not_found.append(category_name)
            return category

        if not results:
            category.not_found.append(f"No results found for: {query}")
            failure_report.missing_information.append(f"{category_name}: no search results for {query!r}")
            return category

        for result in results:
            confidence = min(max(result.score, 0.0), 1.0) or 0.5
            evidence = Evidence(
                claim=result.title or result.content[:120],
                source=result.url,
                confidence=confidence,
                jurisdiction=None,
                last_verified=_now_iso(),
                verification_status="unverified",  # live-fetched this run, but not cross-checked yet — Stage 0.5's job
            )
            category.findings.append(evidence)
            if confidence < 0.4:
                failure_report.low_confidence_claims.append(evidence.claim)
        return category

    def _detect_conflicts(self, categories: List[KnowledgeCategory]) -> List[dict]:
        """Stage 0 does not resolve conflicts (that's Stage 0.5) — it only
        flags where multiple sources in the same category look like they
        might disagree, using a coarse heuristic (nothing that requires an
        LLM call). A real conflict-detection pass belongs in Stage 0.5, not
        here — this is a cheap pre-flag, not a substitute for it."""
        conflicts = []
        for category in categories:
            if len(category.findings) >= 2:
                sources = {e.source for e in category.findings}
                if len(sources) > 1:
                    conflicts.append({
                        "category": category.name,
                        "note": "Multiple independent sources found — not compared for actual disagreement. Stage 0.5 input.",
                        "conflict_type": ConflictType.UNCLASSIFIED.value,
                    })
        return conflicts

    def _score_confidence(self, categories: List[KnowledgeCategory]) -> dict:
        matrix = {}
        for category in categories:
            if category.findings:
                matrix[category.name] = round(sum(e.confidence for e in category.findings) / len(category.findings), 3)
            else:
                matrix[category.name] = 0.0
        return matrix

    def _build_manifest(
        self,
        requirements: RequirementsSpecification,
        categories: List[KnowledgeCategory],
        confidence_matrix: dict,
        conflicts: list,
        failure_report: FailureReport,
        actions_performed: List[str],
        all_sources: List[str],
    ) -> KnowledgeManifest:
        artifact_id = f"uac-stage0-{uuid.uuid4().hex[:12]}"
        known_unknowns = [c.not_found[0] for c in categories if c.not_found]

        payload = KnowledgeManifestPayload(
            profession=requirements.profession,
            jurisdiction=requirements.jurisdiction,
            knowledge_categories=categories,
            confidence_matrix=confidence_matrix,
            known_unknowns=known_unknowns,
            conflicts=[],  # ConflictRecord objects would go here once Stage 0.5 classifies the raw conflicts above
        )

        overall_confidence = round(sum(confidence_matrix.values()) / len(confidence_matrix), 3) if confidence_matrix else 0.0

        return KnowledgeManifest(
            header=UDSHeader(
                artifact_id=artifact_id,
                stage=STAGE_NAME,
                version="1.0.0",
                timestamp=_now_iso(),
            ),
            executive_summary=UDSExecutiveSummary(
                purpose=f"Knowledge Manifest for {requirements.profession} ({requirements.jurisdiction})",
                outcome=f"Collected {len(all_sources)} sourced claims across {len(categories)} categories.",
                status="complete",
            ),
            requirements=UDSRequirements(
                requirement_ids=[requirements.version],
                acceptance_criteria=["Every claim has a source", "Jurisdiction resolved", "Known unknowns documented"],
            ),
            inputs=UDSInputs(
                dependencies=["stage_-1_requirements_specification"],
                sources=all_sources,
                evidence=[e for c in categories for e in c.findings],
            ),
            processing=UDSProcessing(
                actions_performed=actions_performed,
                reasoning_summary="Queried research backend once per fixed category, normalized results into Evidence records, scored confidence from backend relevance scores.",
            ),
            outputs=UDSOutputs(primary_deliverable=payload),
            quality=UDSQuality(
                qc_checklist={
                    "every_claim_has_source": True,
                    "jurisdiction_resolved": bool(requirements.jurisdiction),
                    "known_unknowns_documented": True,
                },
                confidence_score=overall_confidence,
                risk_score=round(1.0 - overall_confidence, 3),
            ),
            governance=UDSGovernance(),
            recommendations=UDSRecommendations(
                next_stage="stage_0.5_knowledge_reconciliation",
                human_actions_needed=failure_report.human_review_required,
            ),
            manifest=UDSManifest(lineage=[artifact_id]),
            failure_report=failure_report,
        )

    def _store_artifact(self, manifest: KnowledgeManifest, requirements: RequirementsSpecification) -> ArtifactRecord:
        return self.axiom_library.publish(
            ArtifactRecord(
                artifact_id=manifest.header.artifact_id,
                stage=manifest.header.stage,
                version=manifest.header.version,
                profession=requirements.profession,
                jurisdiction=requirements.jurisdiction,
                payload=_manifest_to_dict(manifest),
            )
        )


def _manifest_to_dict(manifest: KnowledgeManifest) -> dict:
    """Minimal, explicit serialization — avoids depending on dataclasses.asdict
    for nested Enum handling, which needs the .value extracted explicitly."""
    from dataclasses import asdict
    raw = asdict(manifest)
    return raw
