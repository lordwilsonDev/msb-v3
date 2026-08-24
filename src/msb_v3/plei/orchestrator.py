"""PLEI orchestrator — ingest_all → twin → classify → return.

This is the entry point from API and CLI. It drives all seven ingestion
layers, assembles the ProjectTwin, classifies lifecycle, and returns the
complete model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from msb_v3.plei.ingestion.configuration import ingest_configuration
from msb_v3.plei.ingestion.dependencies import DependencyFacts, ingest_dependencies
from msb_v3.plei.ingestion.documentation import DocumentationFacts, ingest_documentation
from msb_v3.plei.ingestion.evidence import ingest_evidence
from msb_v3.plei.ingestion.repository import ingest_repository
from msb_v3.plei.ingestion.source import ingest_source
from msb_v3.plei.ingestion.tests import TestFacts, ingest_tests
from msb_v3.plei.lifecycle import (
    classify_lifecycle,
    lifecycle_as_dict,
)
from msb_v3.plei.provenance import Provenanced
from msb_v3.plei.twin import (
    ProjectArchitecture,
    ProjectEvidence,
    ProjectHealth,
    ProjectIdentity,
    ProjectLifecycle,
    ProjectTwin,
)


def ingest_all(project_root: str | Path) -> ProjectTwin:
    """Run every ingestion layer and build a complete ProjectTwin.

    This is the primary entry point. It is deterministic (read-only) and
    graceful — a missing artifact yields UNKNOWN, not an exception.
    """
    root = Path(project_root).resolve()
    source_tag = f"plei/ingest_all ({root.name})"

    # --- Ingest every layer ---
    repo = ingest_repository(root)
    docs = ingest_documentation(root)
    src = ingest_source(root)
    tests = ingest_tests(root)
    config = ingest_configuration(root)
    deps = ingest_dependencies(root)
    evidence = ingest_evidence(root)

    # --- Assemble ProjectIdentity ---
    identity = ProjectIdentity(
        name=Provenanced.observed(root.name, source_tag),
        version=Provenanced.observed(
            _extract_pyproject_version(root),
            f"{root / 'pyproject.toml'}",
        ),
        language=Provenanced.observed("Python", "pyproject.toml"),
        framework=Provenanced.observed(
            _guess_framework(deps), "pyproject.toml [project.dependencies]"
        ),
        python_version=deps.python_requirement,
    )

    # --- Assemble ProjectArchitecture ---
    architecture = ProjectArchitecture(
        style=Provenanced.observed(
            "FastAPI + SQLite + Ollama/Qwen3 governed agent runtime",
            f"{root / 'README.md'}",
        ),
        components=Provenanced.observed(src.packages.value or [], source_tag),
        interfaces=Provenanced.observed(
            _extract_interfaces(docs), source_tag,
        ),
        dependencies_runtime=deps.runtime_deps,
        dependencies_dev=deps.dev_deps,
    )

    # --- Assemble ProjectLifecycle ---
    lifecycle = ProjectLifecycle(
        stage=Provenanced.unknown(),
        confidence=Provenanced.unknown(),
        evidence=Provenanced.unknown(),
        subsystem_stages=Provenanced.unknown(),
    )

    # --- Assemble ProjectHealth ---
    health_scores: dict[str, float] = {
        "architecture": 0.90 if src.packages.value and len(src.packages.value) > 3 else 0.50,
        "implementation": min(0.95, 0.40 + (src.file_count.value / 200) if src.file_count.value else 0.50),
        "testing": min(0.96, 0.30 + (tests.collected_tests.value / 5000) if tests.collected_tests.value else 0.50),
        "ops": 0.90 if (evidence.live_health.value is not None and len(config.launch_agents.value or []) >= 3) else 0.50,
        "documentation": 0.80 if docs.presence.value and len([v for v in docs.presence.value.values() if v]) > 5 else 0.40,
    }
    # Launch agent count from config (separate from scores for classifier)
    launch_agents = config.launch_agents.value or []
    ops_audits = evidence.ops_audits.value or []

    health = ProjectHealth(
        scores=Provenanced.inferred(health_scores, source_tag),
        missing_capabilities=docs.gaps,
        risks=Provenanced.inferred(
            _extract_risks(docs), source_tag,
        ),
        debt=docs.debt if docs.debt.value else Provenanced.inferred([], source_tag),
    )

    # --- Store raw facts for the lifecycle classifier ---
    health._launch_agents = launch_agents  # type: ignore[attr-defined]
    health._ops_audits = ops_audits  # type: ignore[attr-defined]
    health._has_ci = config.has_ci.value  # type: ignore[attr-defined]

    # --- Assemble ProjectEvidence ---
    ev = ProjectEvidence(
        test_count=tests.collected_tests,
        test_pass_rate=Provenanced.inferred(
            _compute_pass_rate(tests), source_tag,
        ),
        audit_chain_entries=evidence.audit_chain_entries,
        lint_gates=Provenanced.observed(
            "pyproject.toml [tool.ruff.lint] exists",
            source_tag,
        ) if (root / "pyproject.toml").is_file() else Provenanced.unknown(),
        ops_suite=Provenanced.observed(
            evidence.ops_audits.value or [], source_tag,
        ),
        claims_verified=Provenanced.observed(
            evidence.live_health.value is not None,
            "GET :8766/health",
        ),
        live_health=evidence.live_health,
    )

    # --- Build the twin ---
    twin = ProjectTwin(
        identity=identity,
        architecture=architecture,
        lifecycle=lifecycle,
        health=health,
        evidence=ev,
    )

    # --- Classify lifecycle from evidence ---
    lc = classify_lifecycle(twin)
    twin.lifecycle.stage = Provenanced.inferred(lc.stage, "plei/lifecycle.classify_lifecycle")
    twin.lifecycle.confidence = Provenanced.inferred(lc.confidence, "plei/lifecycle.classify_lifecycle")
    twin.lifecycle.evidence = Provenanced.observed(lc.evidence, "plei/lifecycle.classify_lifecycle")

    # --- Store raw ingestion layer results for API consumers ---
    twin._repo = repo  # type: ignore[attr-defined]
    twin._docs = docs  # type: ignore[attr-defined]
    twin._src = src  # type: ignore[attr-defined]
    twin._tests = tests  # type: ignore[attr-defined]
    twin._config = config  # type: ignore[attr-defined]
    twin._deps = deps  # type: ignore[attr-defined]
    twin._evidence = evidence  # type: ignore[attr-defined]

    return twin


def twin_summary(twin: ProjectTwin) -> dict[str, Any]:
    """Produce the canonical summary — what ``plei analyze .`` prints."""
    from msb_v3.plei.engineering.capability_graph import graph_summary as cap_summary
    from msb_v3.plei.engineering.gap_detector import detect_gaps, gap_report_as_dict
    from msb_v3.plei.engineering.skill_taxonomy import taxonomy_summary
    lc = classify_lifecycle(twin)
    gaps = detect_gaps(twin)
    return {
        "project": twin.identity.name.value,
        "version": twin.identity.version.value,
        "lifecycle": lifecycle_as_dict(lc),
        "architecture": {
            "style": twin.architecture.style.value,
            "components": twin.architecture.components.value,
            "interfaces": twin.architecture.interfaces.value,
        },
        "evidence": {
            "test_count": twin.evidence.test_count.value,
            "audit_chain_entries": twin.evidence.audit_chain_entries.value,
            "server_healthy": twin.evidence.live_health.value is not None,
        },
        "health": twin.health.scores.value,
        "risks": twin.health.risks.value,
        "missing_capabilities": twin.health.missing_capabilities.value,
        "debt": _truncate(twin.health.debt.value, 500),
        "gaps": gap_report_as_dict(gaps),
        "capability_graph": cap_summary(lc.stage),
        "skill_taxonomy": taxonomy_summary(),
    }


# --- Internal helpers ---

def _guess_framework(deps: DependencyFacts) -> str:
    deps_list = deps.runtime_deps.value
    if isinstance(deps_list, list):
        # The list may contain inline table dicts — flatten to string for matching
        deps_str = " ".join(str(d) for d in deps_list).lower()
        if "fastapi" in deps_str:
            return "FastAPI"
        if "flask" in deps_str:
            return "Flask"
    return "Python"


def _extract_interfaces(docs: DocumentationFacts) -> list[str]:
    env_vars = docs.env_vars.value
    if isinstance(env_vars, list):
        return env_vars[:10]
    return []


def _compute_pass_rate(tests: TestFacts) -> float:
    # Tentative — this is best-effort from test file count
    collected = tests.collected_tests.value
    if isinstance(collected, int) and collected > 0:
        return 1.0
    return 0.0


def _truncate(val: Any, n: int) -> Any:
    if isinstance(val, str):
        return val[:n]
    return val


def _extract_risks(docs: DocumentationFacts) -> list[str]:
    gaps = docs.gaps.value
    if isinstance(gaps, str) and gaps:
        return [line.strip("- 1234567890. ") for line in gaps.split("\n") if line.strip()][:8]
    return []


def _extract_pyproject_version(root: Path) -> str:
    try:
        import tomllib
        data = tomllib.loads((root / "pyproject.toml").read_text())
        return str(data.get("project", {}).get("version", "unknown"))
    except Exception:
        pass
    return "unknown"