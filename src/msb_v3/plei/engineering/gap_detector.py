"""Gap Detector — identify missing capabilities for a project's lifecycle stage.

Compares the ProjectTwin against the capability graph for its current
lifecycle stage. For each missing capability, identifies which skills
could provide it and which providers can execute those skills.

Output is a ranked list of gaps — the decision engine's primary input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from msb_v3.plei.engineering.capability_graph import (
    capabilities_for_stage,
    capability_by_name,
    roles_for_stage,
    skills_for_capability,
)
from msb_v3.plei.engineering.skill_taxonomy import (
    capabilities_covered,
)
from msb_v3.plei.lifecycle import classify_lifecycle
from msb_v3.plei.twin import ProjectTwin


@dataclass(slots=True)
class CapabilityGap:
    """A single gap — a capability the project needs but doesn't fully have."""

    capability: str
    category: str
    criticality: int  # 1–10
    status: str  # "COVERED" | "PARTIAL" | "MISSING"
    available_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    provider_gap: bool = False  # capability has skills but no available provider
    recommendation: str = ""


@dataclass(slots=True)
class GapReport:
    """Full gap analysis for a project at its current lifecycle stage."""

    stage: str
    stage_confidence: float
    total_capabilities_required: int
    covered: int
    partial: int
    missing: int
    gaps: list[CapabilityGap] = field(default_factory=list)
    required_roles: list[dict[str, str]] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)


def detect_gaps(twin: ProjectTwin) -> GapReport:
    """Detect capability gaps for the project at its current lifecycle stage.

    For each capability the lifecycle stage requires:
      1. Check if any installed skill provides it → COVERED
      2. Check if a skill is known but not installed → PARTIAL
      3. No skill known at all → MISSING
      4. Skill exists but no provider available → PARTIAL (provider_gap=True)

    Ranks gaps by criticality (highest first), then by status (MISSING first).
    """
    lc = classify_lifecycle(twin)
    stage = lc.stage
    required_caps = capabilities_for_stage(stage)
    installed_cap_map = capabilities_covered()

    gaps: list[CapabilityGap] = []

    for cap_name in required_caps:
        cap_def = capability_by_name(cap_name)
        category = cap_def.category if cap_def else "engineering"
        criticality = cap_def.criticality if cap_def else 5

        # Skills from the graph (what *could* provide this)
        graph_skills = skills_for_capability(cap_name)
        graph_skill_names = {s.skill_name for s in graph_skills}

        # Skills actually installed
        installed = installed_cap_map.get(cap_name, [])
        installed_names = {s.name for s in installed}

        # Skills known but not installed
        known_but_missing = graph_skill_names - installed_names

        # Provider analysis — can any installed skill actually run?
        provider_gap = False
        available_skills_list: list[str] = []
        if installed_names:
            # Check if at least one provider is available
            try:
                from msb_v3.agent.providers import ProviderRegistry
                reg = ProviderRegistry()
                available = {p.spec.provider_id for p in reg.select(available_only=True)}
                for s in graph_skills:
                    if s.skill_name in installed_names:
                        if any(pid in available for pid in s.provider_ids):
                            available_skills_list.append(s.skill_name)
                if installed_names and not available_skills_list:
                    provider_gap = True
            except ImportError:
                available_skills_list = list(installed_names)

        # Determine status
        if available_skills_list:
            status = "COVERED"
        elif installed_names and provider_gap:
            status = "PARTIAL"
        elif installed_names:
            status = "COVERED"
        elif graph_skill_names:
            status = "PARTIAL"
        else:
            status = "MISSING"

        # Recommendation
        if status == "COVERED":
            recommendation = f"Capability covered by: {', '.join(available_skills_list[:3])}"
        elif status == "PARTIAL" and provider_gap:
            recommendation = (
                f"Skills installed ({', '.join(sorted(installed_names))}) "
                f"but no provider available — check API keys or binary paths"
            )
        elif status == "PARTIAL":
            recommendation = (
                f"Skills known but not installed: {', '.join(sorted(known_but_missing))}. "
                f"Run `npx skills add` for each"
            )
        else:
            recommendation = (
                f"No skill known for {cap_name}. "
                f"Build or install a skill that provides this capability."
            )

        gaps.append(CapabilityGap(
            capability=cap_name,
            category=category,
            criticality=criticality,
            status=status,
            available_skills=available_skills_list,
            missing_skills=sorted(known_but_missing),
            provider_gap=provider_gap,
            recommendation=recommendation,
        ))

    # Sort: MISSING first, then by criticality descending
    status_order = {"MISSING": 0, "PARTIAL": 1, "COVERED": 2}
    gaps.sort(key=lambda g: (status_order.get(g.status, 9), -g.criticality))

    covered = sum(1 for g in gaps if g.status == "COVERED")
    partial = sum(1 for g in gaps if g.status == "PARTIAL")
    missing_count = sum(1 for g in gaps if g.status == "MISSING")

    # Next actions — top 3 gaps
    next_actions: list[str] = []
    for g in gaps[:5]:
        if g.status != "COVERED":
            next_actions.append(
                f"[{g.criticality}/10] {g.status}: {g.capability} — {g.recommendation}"
            )

    return GapReport(
        stage=stage,
        stage_confidence=lc.confidence,
        total_capabilities_required=len(required_caps),
        covered=covered,
        partial=partial,
        missing=missing_count,
        gaps=gaps,
        required_roles=[
            {"name": r.name, "discipline": r.discipline, "description": r.description}
            for r in roles_for_stage(stage)
        ],
        next_actions=next_actions,
    )


def gap_report_as_dict(report: GapReport) -> dict[str, Any]:
    return {
        "stage": report.stage,
        "stage_confidence": report.stage_confidence,
        "total_capabilities_required": report.total_capabilities_required,
        "covered": report.covered,
        "partial": report.partial,
        "missing": report.missing,
        "gaps": [
            {
                "capability": g.capability,
                "category": g.category,
                "criticality": g.criticality,
                "status": g.status,
                "available_skills": g.available_skills,
                "missing_skills": g.missing_skills,
                "provider_gap": g.provider_gap,
                "recommendation": g.recommendation,
            }
            for g in report.gaps
        ],
        "required_roles": report.required_roles,
        "next_actions": report.next_actions,
    }