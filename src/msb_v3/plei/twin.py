"""ProjectTwin — the normalized living model of a project.

A twin is a set of discoveries backed by evidence. It changes as the
project changes — it is not a static snapshot. Every assertion carries a
provenance tag and a source so the reader can trace back to what produced
the assertion.

The twin is the output of ``plei analyze .`` and the input to the lifecycle
classifier, capability graph, and decision engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from msb_v3.plei.provenance import Provenanced


@dataclass(slots=True)
class ProjectIdentity:
    """What the project calls itself."""

    name: Provenanced = field(default_factory=Provenanced.unknown)
    version: Provenanced = field(default_factory=Provenanced.unknown)
    language: Provenanced = field(default_factory=Provenanced.unknown)
    framework: Provenanced = field(default_factory=Provenanced.unknown)
    python_version: Provenanced = field(default_factory=Provenanced.unknown)


@dataclass(slots=True)
class ProjectArchitecture:
    """Structure, components, interfaces, dependencies."""

    style: Provenanced = field(default_factory=Provenanced.unknown)
    components: Provenanced = field(default_factory=Provenanced.unknown)
    interfaces: Provenanced = field(default_factory=Provenanced.unknown)
    dependencies_runtime: Provenanced = field(default_factory=Provenanced.unknown)
    dependencies_dev: Provenanced = field(default_factory=Provenanced.unknown)


@dataclass(slots=True)
class ProjectLifecycle:
    """Lifecycle position per subsystem."""

    stage: Provenanced = field(default_factory=Provenanced.unknown)
    confidence: Provenanced = field(default_factory=Provenanced.unknown)
    evidence: Provenanced = field(default_factory=Provenanced.unknown)
    subsystem_stages: Provenanced = field(default_factory=Provenanced.unknown)


@dataclass
class ProjectHealth:
    """Multidimensional health scores with evidence."""

    scores: Provenanced = field(default_factory=Provenanced.unknown)
    missing_capabilities: Provenanced = field(default_factory=Provenanced.unknown)
    risks: Provenanced = field(default_factory=Provenanced.unknown)
    debt: Provenanced = field(default_factory=Provenanced.unknown)


@dataclass(slots=True)
class ProjectEvidence:
    """What the project can prove about itself."""

    test_count: Provenanced = field(default_factory=Provenanced.unknown)
    test_pass_rate: Provenanced = field(default_factory=Provenanced.unknown)
    audit_chain_entries: Provenanced = field(default_factory=Provenanced.unknown)
    lint_gates: Provenanced = field(default_factory=Provenanced.unknown)
    ops_suite: Provenanced = field(default_factory=Provenanced.unknown)
    claims_verified: Provenanced = field(default_factory=Provenanced.unknown)
    live_health: Provenanced = field(default_factory=Provenanced.unknown)


@dataclass
class ProjectTwin:
    """The complete project model, backed by evidence."""

    identity: ProjectIdentity = field(default_factory=ProjectIdentity)
    architecture: ProjectArchitecture = field(default_factory=ProjectArchitecture)
    lifecycle: ProjectLifecycle = field(default_factory=ProjectLifecycle)
    health: ProjectHealth = field(default_factory=ProjectHealth)
    evidence: ProjectEvidence = field(default_factory=ProjectEvidence)

    def as_dict(self) -> dict[str, Any]:
        """Serializable summary for API consumers."""
        return {
            "identity": {
                k: getattr(self.identity, k).as_dict()
                for k in ("name", "version", "language", "framework", "python_version")
            },
            "architecture": {
                k: getattr(self.architecture, k).as_dict()
                for k in ("style", "components", "interfaces", "dependencies_runtime", "dependencies_dev")
            },
            "lifecycle": {
                k: getattr(self.lifecycle, k).as_dict()
                for k in ("stage", "confidence", "evidence", "subsystem_stages")
            },
            "health": {
                k: getattr(self.health, k).as_dict()
                for k in ("scores", "missing_capabilities", "risks", "debt")
            },
            "evidence": {
                k: getattr(self.evidence, k).as_dict()
                for k in ("test_count", "test_pass_rate", "audit_chain_entries",
                           "lint_gates", "ops_suite", "claims_verified", "live_health")
            },
        }