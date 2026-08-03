"""Data models for UAC Stage 0 — Knowledge Acquisition Engine.

Reconciliation note (2026-08-02): the Stage 0 blueprint's "Knowledge
Manifest" field list (Artifact ID, Stage, Version, Timestamp, Profession,
Jurisdiction, Executive Summary, ...) does not match the frozen UAC v1.0
Universal Deliverable Specification's shape (Header/Executive Summary/
Requirements/Inputs/Processing/Outputs/Quality/Governance/Recommendations/
Manifest) field-for-field, even though the blueprint explicitly says
"the output should follow the Universal Deliverable Specification."

Resolved here rather than left ambiguous going into code: KnowledgeManifest
IS the UDS envelope for Stage 0's output. Stage-0-specific content
(profession, jurisdiction, knowledge categories, confidence matrix, known
unknowns, conflicts) lives in a KnowledgeManifestPayload nested inside
`outputs.primary_deliverable` — not as new top-level UDS fields. Every other
UAC stage's output should follow the same pattern: reuse the UDS envelope,
put stage-specific content in outputs.primary_deliverable, don't invent a
new top-level shape per stage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ConflictType(str, Enum):
    """From the Stage 0.5 conflict taxonomy fix (universal-agent-creator-v1.0.md).
    Stage 0 records conflicts using this taxonomy even though resolving them is
    Stage 0.5's job — Stage 0 shouldn't silently pick a winner itself."""

    SOURCE_CONTRADICTION = "source_contradiction"
    JURISDICTIONAL_VARIANCE = "jurisdictional_variance"
    QUALIFIED_CONSENSUS = "qualified_consensus"
    UNCLASSIFIED = "unclassified"  # Stage 0 found a conflict but hasn't run reconciliation yet


@dataclass
class ConflictRecord:
    description: str
    conflicting_claims: List[str]
    conflict_type: ConflictType = ConflictType.UNCLASSIFIED
    jurisdiction: Optional[str] = None


@dataclass
class Evidence:
    """One sourced claim. Every claim in a Knowledge Manifest must have one of
    these — "never guess" is enforced by this being mandatory, not optional."""

    claim: str
    source: str
    confidence: float  # 0.0-1.0
    jurisdiction: Optional[str] = None
    last_verified: Optional[str] = None  # ISO8601 — freshness, distinct from confidence
    verification_status: str = "unverified"  # unverified | verified | disputed
    conflicts: List[str] = field(default_factory=list)  # claim text of any conflicting claims found


@dataclass
class KnowledgeCategory:
    """One of the blueprint's fixed research categories (Profession Overview,
    Workflow Knowledge, Required Knowledge, Software Ecosystem, Human Skills,
    Failure Modes, Automation Opportunities)."""

    name: str
    findings: List[Evidence] = field(default_factory=list)
    not_found: List[str] = field(default_factory=list)  # explicitly what wasn't found, not silently omitted


@dataclass
class RequirementsSpecification:
    """Stage -1 output, consumed here as Stage 0's Requirements input — Stage 0
    cannot run without one of these per the Human Availability Gate fix."""

    profession: str
    jurisdiction: str
    industry: Optional[str] = None
    organization_size: Optional[str] = None
    constraints: List[str] = field(default_factory=list)
    existing_tools: List[str] = field(default_factory=list)
    risk_tolerance: Optional[str] = None
    version: str = "1.0.0"


@dataclass
class KnowledgeManifestPayload:
    """Stage-0-specific content, nested inside the UDS envelope's
    outputs.primary_deliverable — see module docstring for why."""

    profession: str
    jurisdiction: str
    knowledge_categories: List[KnowledgeCategory] = field(default_factory=list)
    confidence_matrix: Dict[str, float] = field(default_factory=dict)  # category name -> avg confidence
    known_unknowns: List[str] = field(default_factory=list)
    conflicts: List[ConflictRecord] = field(default_factory=list)


@dataclass
class FailureReport:
    """Cross-Cutting System 11, Stage-0 instantiation. Field list follows the
    Stage 0 blueprint's fuller version, not the original CCS-11 wording it
    was frozen with — flagged here rather than silently treated as identical:
    CCS-11 said {assumptions, what wasn't found, low confidence, needs human
    review, could break later}. This blueprint's version adds Conflicting
    Sources, Research Limitations, Suggested Follow-up, and Estimated
    Compiler Risk as distinct fields. Implementing the fuller version since
    it's a strict superset for Stage 0's purposes; CCS-11's canonical
    definition should probably be updated to match rather than left to drift
    — not done here, flagged for the spec, not silently patched."""

    missing_information: List[str] = field(default_factory=list)
    low_confidence_claims: List[str] = field(default_factory=list)
    conflicting_sources: List[str] = field(default_factory=list)
    human_review_required: List[str] = field(default_factory=list)
    research_limitations: List[str] = field(default_factory=list)
    suggested_follow_up: List[str] = field(default_factory=list)
    estimated_compiler_risk: str = "unknown"  # low | medium | high | unknown


# --- Universal Deliverable Specification envelope (frozen 2026-08-02) ---


@dataclass
class UDSHeader:
    artifact_id: str
    stage: str
    version: str
    timestamp: str
    author: str = "stage_0_knowledge_acquisition"


@dataclass
class UDSExecutiveSummary:
    purpose: str
    outcome: str
    status: str  # draft | complete | halted


@dataclass
class UDSRequirements:
    requirement_ids: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)


@dataclass
class UDSInputs:
    dependencies: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)


@dataclass
class UDSProcessing:
    actions_performed: List[str] = field(default_factory=list)
    reasoning_summary: str = ""


@dataclass
class UDSOutputs:
    primary_deliverable: KnowledgeManifestPayload
    supporting_files: List[str] = field(default_factory=list)


@dataclass
class UDSQuality:
    qc_checklist: Dict[str, bool] = field(default_factory=dict)
    validation_results: Dict[str, Any] = field(default_factory=dict)
    confidence_score: float = 0.0
    risk_score: float = 0.0


@dataclass
class UDSGovernance:
    approvals: List[str] = field(default_factory=list)
    policy_checks: List[str] = field(default_factory=list)
    security_checks: List[str] = field(default_factory=list)


@dataclass
class UDSRecommendations:
    next_stage: str = ""
    human_actions_needed: List[str] = field(default_factory=list)


@dataclass
class UDSManifest:
    checksum: str = ""
    digital_signature: str = ""
    lineage: List[str] = field(default_factory=list)


@dataclass
class KnowledgeManifest:
    """The Universal Deliverable Specification envelope, populated for Stage 0."""

    header: UDSHeader
    executive_summary: UDSExecutiveSummary
    requirements: UDSRequirements
    inputs: UDSInputs
    processing: UDSProcessing
    outputs: UDSOutputs
    quality: UDSQuality
    governance: UDSGovernance
    recommendations: UDSRecommendations
    manifest: UDSManifest
    failure_report: FailureReport = field(default_factory=FailureReport)
