"""Skill Taxonomy — catalog installed skills with capability bindings.

Walks ``~/.agents/skills/`` and builds a catalog of every installed skill:
name, description, capabilities it provides, preferred providers, and
installation status. Integrates with the ProviderRegistry so skills are
routable to the best available provider.

This is the link between the capability graph (what the project needs) and
the provider registry (what can execute it).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SKILLS_HOME = Path(os.environ.get("MSB_SKILLS_DIR", str(Path.home() / ".agents" / "skills")))


@dataclass(slots=True)
class SkillRecord:
    """One installed skill with its metadata and capability bindings."""

    name: str
    description: str = ""
    path: str = ""
    capabilities: tuple[str, ...] = ()
    provider_ids: tuple[str, ...] = ()
    installation: str = "unknown"  # "installed" | "built-in" | "not-installed"


def _read_frontmatter(filepath: Path) -> dict[str, str]:
    """Extract YAML frontmatter from a SKILL.md file."""
    try:
        content = filepath.read_text()
    except Exception:
        return {}
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    frontmatter = content[3:end].strip()
    result: dict[str, str] = {}
    for line in frontmatter.split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip()] = val.strip().strip('"').strip("'")
    return result


def _infer_capabilities(name: str, description: str) -> tuple[str, ...]:
    """Map a skill name/description to capability names from the capability graph.

    This is a deterministic mapping, not an ML classification. Every skill
    that provides a capability in ``capability_graph.py`` appears here.
    """
    caps: list[str] = []
    desc_lower = description.lower()
    name_lower = name.lower()

    # These are the explicit bindings from capability_graph.CAPABILITY_SKILLS
    # Replicated here for catalog consistency:
    if name in {
        "sovereign-project-lifecycle-orchestrator",
    }:
        caps.extend(["mission_definition", "problem_analysis", "architecture_design",
                     "health_monitoring", "documentation"])

    if name in {"srse-analyzing-implementations"}:
        caps.extend(["code_review", "problem_analysis", "failure_testing"])

    if name in {"interchangeable-components"}:
        caps.extend(["component_decomposition", "capability_planning"])

    if name in {"auditing-solo-repos"}:
        caps.append("code_review")

    if name in {"spec-driven-workflow"}:
        caps.append("testing")

    if name in {"mutating-skill"}:
        caps.append("testing")

    if name in {"ship-gate"}:
        caps.extend(["security_audit", "incident_response"])

    if name in {"env-secrets-manager"}:
        caps.append("security_audit")

    if name in {"sovereign-verification"}:
        caps.extend(["security_audit", "backup_recovery", "audit_logging"])

    if name in {"sovereign-ghl-infrastructure"}:
        caps.append("security_hardening")

    if name in {"srse-calibrating-confidence"}:
        caps.append("observability")

    if name in {"srse-synthesizing-cross-domain", "srse-forecasting-scenarios"}:
        caps.append("research_synthesis")

    if name in {"srse-generating-frameworks"}:
        caps.append("roadmap_planning")

    if name in {"srse-validating-adversarially", "srse-inverting-assumptions"}:
        caps.append("adversarial_testing")

    if name in {"srse-designing-experiments"}:
        caps.append("performance_testing")

    if name in {"agent-harness"}:
        caps.append("ci_cd_pipeline")

    if name in {"agent-designer", "agent-workflow-designer"}:
        caps.append("architecture_design")

    if name in {"notebooklm"}:
        caps.append("research_synthesis")

    if name in {"workspace-memory", "freebuff-pzs-memory"}:
        caps.append("memory_management")

    if name in {"vault-search", "vault-check-first"}:
        caps.append("vault_search")

    if name in {"mcp-server-builder"}:
        caps.append("mcp_development")

    if name in {"process-mapper"}:
        caps.append("process_design")

    if name in {"loop-library"}:
        caps.append("capability_planning")

    if name in {"system-connector"}:
        caps.append("integration_testing")

    if name in {"ego-browser"}:
        caps.append("browser_automation")

    # n8n cluster
    if "n8n" in name_lower:
        caps.append("n8n_expertise")

    # automation
    if "automation" in desc_lower or "n8n" in desc_lower:
        if "automation_development" not in caps:
            caps.append("automation_development")

    return tuple(sorted(set(caps)))


def catalog_skills() -> list[SkillRecord]:
    """Walk ~/.agents/skills/ and build a catalog of every installed skill."""
    records: list[SkillRecord] = []
    if not SKILLS_HOME.is_dir():
        return records

    for skill_dir in sorted(SKILLS_HOME.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            # Symlinked skills (like ego-browser)
            real_skill_md = skill_dir.resolve() / "SKILL.md" if skill_dir.is_symlink() else None
            if real_skill_md and real_skill_md.is_file():
                skill_md = real_skill_md

        if not skill_md.is_file():
            continue

        fm = _read_frontmatter(skill_md)
        name = fm.get("name", skill_dir.name)
        description = fm.get("description", "")
        capabilities = _infer_capabilities(name, description)

        records.append(SkillRecord(
            name=name,
            description=description[:200],
            path=str(skill_md),
            capabilities=capabilities,
            provider_ids=("api.deepseek", "api.anthropic", "local.slice"),
            installation="installed",
        ))

    return records


def skills_for_capability(capability_name: str) -> list[SkillRecord]:
    """Find all installed skills that provide a given capability."""
    return [
        s for s in catalog_skills()
        if capability_name in s.capabilities
    ]


def capabilities_covered() -> dict[str, list[SkillRecord]]:
    """Map of capability → skills that provide it (from installed skills only)."""
    result: dict[str, list[SkillRecord]] = {}
    for skill in catalog_skills():
        for cap in skill.capabilities:
            if cap not in result:
                result[cap] = []
            result[cap].append(skill)
    return result


def taxonomy_summary() -> dict[str, Any]:
    """Summary of the entire skill taxonomy."""
    records = catalog_skills()
    return {
        "total_skills": len(records),
        "total_capabilities_covered": len(capabilities_covered()),
        "skills": [
            {
                "name": r.name,
                "capabilities": list(r.capabilities),
                "providers": list(r.provider_ids),
            }
            for r in records
        ],
        "capability_coverage": {
            cap: [s.name for s in skills]
            for cap, skills in capabilities_covered().items()
        },
    }