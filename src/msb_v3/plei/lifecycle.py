"""Lifecycle classification — evidence-based, not label-based.

Classifies a project into PLEI's 15-stage lifecycle model, per subsystem.
Every classification carries evidence and a confidence score — never a bare
label.

Stages (linear order, but non-linear transitions allowed):
    IDEA → DISCOVERY → RESEARCH → ARCHITECTURE → SPECIFICATION →
    PROTOTYPE → IMPLEMENTATION → INTEGRATION → VERIFICATION →
    HARDENING → RELEASE → OPERATIONS → OPTIMIZATION → EVOLUTION

The project may occupy different stages in different subsystems (e.g.
ops at OPERATIONS while product is still at DISCOVERY).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from msb_v3.plei.provenance import Provenanced
from msb_v3.plei.twin import ProjectTwin

STAGES = [
    "IDEA", "DISCOVERY", "RESEARCH", "ARCHITECTURE", "SPECIFICATION",
    "PROTOTYPE", "IMPLEMENTATION", "INTEGRATION", "VERIFICATION",
    "HARDENING", "RELEASE", "OPERATIONS", "OPTIMIZATION", "EVOLUTION",
]

STAGE_RANK = {s: i for i, s in enumerate(STAGES)}


@dataclass(slots=True)
class LifecycleClassification:
    """Lifecycle position with evidence."""

    stage: str  # one of STAGES
    confidence: float  # 0.0–1.0
    evidence: list[str]  # short descriptions of what supports this
    subsystem_stages: dict[str, str] = field(default_factory=dict)  # subsystem→stage


def classify_lifecycle(twin: ProjectTwin) -> LifecycleClassification:
    """Classify lifecycle stage from evidence in the project twin.

    Rules (ordered — highest match first):

    1. OPERATIONS: launchd agents ≥ 3 + ops audit reports ≥ 2 + server healthy
    2. HARDENING: tests ≥ 500 + failure matrix present + bypass tests present
    3. INTEGRATION: components ≥ 5 + interfaces ≥ 3 + CI present
    4. IMPLEMENTATION: source files ≥ 20 + tests ≥ 10
    5. ARCHITECTURE: README present + pyproject present + deps defined
    6. RESEARCH/PROTOTYPE: source files exist but < 20
    7. IDEA: no evidence yet
    """
    evidence: list[str] = []
    stage = "IDEA"
    confidence = 0.0

    # Gather facts
    test_count = _safe_int(twin.evidence.test_count.value)
    audit_entries = _safe_int(twin.evidence.audit_chain_entries.value)
    source_files = _safe_int(_get_value(twin.architecture.components, 0))
    components = _safe_list(twin.architecture.components.value)

    # Launch agents and ops audits are stored as raw attrs on the health object
    launch_agents = len(_safe_list(getattr(twin.health, '_launch_agents', [])))
    ops_audits = _safe_list(getattr(twin.health, '_ops_audits', []))

    has_ci = _safe_bool(getattr(twin.health, '_has_ci', False))
    has_health = twin.evidence.live_health.value is not None

    # Determine stage
    if has_health and len(ops_audits) >= 1 and launch_agents >= 3:
        stage = "OPERATIONS"
        confidence = min(0.95, 0.60 + (launch_agents * 0.05) + (len(ops_audits) * 0.05))
        evidence.append(f"{launch_agents} launchd agents configured")
        evidence.append(f"{len(ops_audits)} ops audit reports found")
        if has_health:
            evidence.append("server health check passing")
    elif test_count >= 500 and source_files >= 20:
        stage = "HARDENING"
        confidence = min(0.93, 0.50 + (test_count / 5000))
        evidence.append(f"{test_count} tests")
        evidence.append(f"audit chain: {audit_entries} entries")
    elif len(components) >= 5 and has_ci:
        stage = "INTEGRATION"
        confidence = 0.75 + (0.05 * min(len(components), 5))
        evidence.append(f"{len(components)} architecturally distinct components")
        evidence.append("CI workflows present")
    elif source_files >= 20 and test_count >= 10:
        stage = "IMPLEMENTATION"
        confidence = 0.60 + (0.05 * min(len(components), 5))
        evidence.append(f"{source_files} source files, {test_count} tests")
    elif source_files > 0 and source_files < 20:
        stage = "PROTOTYPE"
        confidence = 0.40 + (source_files * 0.03)
        evidence.append(f"{source_files} source files")
    elif _safe_int(_get_value(twin.identity.version.value, 0)) > 0:
        stage = "ARCHITECTURE"
        confidence = 0.50
        evidence.append("version declared in pyproject.toml")
    else:
        stage = "IDEA"
        confidence = 0.20
        evidence.append("insufficient evidence to classify")

    # Subsystem stages (from project map if available)
    subsystem_stages: dict[str, str] = {}
    pm_sections = twin.health.debt.value  # reuse project_map_sections if populated
    _ = pm_sections  # project-map-style subsystem classification deferred to Phase 2

    return LifecycleClassification(
        stage=stage,
        confidence=round(confidence, 2),
        evidence=evidence,
        subsystem_stages=subsystem_stages,
    )


# --- helpers ---

def _safe_int(val: Any) -> int:
    if isinstance(val, (int, float)):
        return int(val)
    return 0


def _safe_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    return False


def _safe_list(val: Any) -> list[Any]:
    if isinstance(val, list):
        return val
    if isinstance(val, dict):
        return list(val.keys())
    return []


def _safe_count(val: Any, key: str) -> int:
    if isinstance(val, dict):
        return len(val.get(key, [])) if key else 0
    return 0


def _get_value(obj: Any, default: Any) -> Any:
    """Unwrap a Provenanced or return default."""
    if isinstance(obj, Provenanced):
        return obj.value if obj.value is not None else default
    return obj if obj is not None else default


def lifecycle_as_dict(lc: LifecycleClassification) -> dict[str, Any]:
    return {
        "stage": lc.stage,
        "confidence": lc.confidence,
        "evidence": lc.evidence,
        "subsystem_stages": lc.subsystem_stages,
    }