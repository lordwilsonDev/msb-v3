"""Capability Graph — stage → capability → skill → role → provider.

The graph answers three questions:
    1. What capabilities does this lifecycle stage require?
    2. Which skills provide each capability?
    3. Which providers can execute each skill?

It is a data structure, not a learned model — every mapping is explicit and
auditable. The graph is the bridge between lifecycle classification (Phase 1)
and gap detection (Phase 2).

Design rule: the graph lives in code, not config, because these mappings
embody engineering judgment. They change when the project's architecture
changes, not at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Capability:
    """A named capability the project needs."""

    name: str
    category: str  # "engineering", "security", "ops", "research", "product"
    description: str
    criticality: int = 5  # 1–10, higher = more critical
    required_by_stages: tuple[str, ...] = ()


@dataclass(slots=True)
class SkillBinding:
    """A skill that provides a capability, with provider routing info."""

    skill_name: str
    provider_ids: tuple[str, ...]  # e.g. ("api.deepseek", "api.anthropic", "local.slice")
    description: str
    installation: str  # "built-in" | "installed" | "not-installed"


@dataclass(slots=True)
class Role:
    """An engineering role required at a lifecycle stage."""

    name: str
    discipline: str  # "engineering", "security", "product", "ops", "research"
    description: str
    required_at_stages: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# The graph — explicit, auditable mappings
# ---------------------------------------------------------------------------

# --- STAGE → REQUIRED CAPABILITIES ---

STAGE_CAPABILITIES: dict[str, list[str]] = {
    "IDEA": ["mission_definition", "problem_analysis"],
    "DISCOVERY": ["mission_definition", "problem_analysis", "user_research", "competitive_analysis"],
    "RESEARCH": ["problem_analysis", "literature_review", "benchmark_evaluation", "feasibility_study"],
    "ARCHITECTURE": ["architecture_design", "component_decomposition", "interface_definition",
                     "dependency_mapping", "security_model", "data_model"],
    "SPECIFICATION": ["spec_writing", "acceptance_criteria", "test_strategy", "security_requirements"],
    "PROTOTYPE": ["rapid_prototyping", "benchmark_evaluation", "feasibility_study"],
    "IMPLEMENTATION": ["code_generation", "code_review", "testing", "documentation", "dependency_management"],
    "INTEGRATION": ["ci_cd_pipeline", "integration_testing", "component_wiring", "contract_testing"],
    "VERIFICATION": ["test_automation", "adversarial_testing", "benchmark_evaluation",
                     "security_audit", "performance_testing"],
    "HARDENING": ["failure_testing", "chaos_engineering", "security_hardening", "recovery_testing",
                  "observability", "stress_testing"],
    "RELEASE": ["release_management", "deployment_automation", "rollback_strategy",
               "monitoring_setup", "user_documentation"],
    "OPERATIONS": ["health_monitoring", "incident_response", "backup_recovery",
                   "capacity_planning", "security_patching", "audit_logging"],
    "OPTIMIZATION": ["performance_profiling", "bottleneck_analysis", "cost_optimization",
                     "architecture_review"],
    "EVOLUTION": ["roadmap_planning", "capability_planning", "technology_assessment",
                  "migration_strategy"],
}

# --- CAPABILITY → SKILLS ---

CAPABILITY_SKILLS: dict[str, list[SkillBinding]] = {
    "mission_definition": [
        SkillBinding("sovereign-project-lifecycle-orchestrator", ("api.deepseek", "api.anthropic"),
                      "Lifecycle orchestration with mission/architecture reconstruction", "installed"),
    ],
    "problem_analysis": [
        SkillBinding("srse-analyzing-implementations", ("api.deepseek", "api.anthropic"),
                      "Production-readiness evaluation of libraries and systems", "installed"),
        SkillBinding("sovereign-project-lifecycle-orchestrator", ("api.deepseek", "api.anthropic"),
                      "Project archaeology and gap identification", "installed"),
    ],
    "architecture_design": [
        SkillBinding("sovereign-project-lifecycle-orchestrator", ("api.deepseek", "api.anthropic"),
                      "Architecture evolution with CURRENT/TARGET/TRANSITION", "installed"),
        SkillBinding("agent-designer", ("api.deepseek", "api.anthropic", "dsh.headless"),
                      "Multi-agent system design with orchestration patterns", "installed"),
        SkillBinding("agent-workflow-designer", ("api.deepseek", "api.anthropic", "dsh.headless"),
                      "Production-grade multi-agent workflow design", "installed"),
    ],
    "component_decomposition": [
        SkillBinding("interchangeable-components", ("api.deepseek", "api.anthropic"),
                      "Capability seam pattern — Definition/Provider/Consumer/Registry", "installed"),
    ],
    "code_generation": [
        SkillBinding("dsh.headless", ("dsh.headless",),
                      "DeepSeek Harness headless agent — plugin-based code generation", "built-in"),
        SkillBinding("local.slice", ("local.slice",),
                      "Local sovereign slice — governed code generation", "built-in"),
    ],
    "code_review": [
        SkillBinding("srse-analyzing-implementations", ("api.deepseek", "api.anthropic"),
                      "Code review with production-readiness assessment", "installed"),
        SkillBinding("auditing-solo-repos", ("api.deepseek", "api.anthropic"),
                      "Honest solo-repo evaluation — real craft vs ceremony", "installed"),
    ],
    "testing": [
        SkillBinding("spec-driven-workflow", ("api.deepseek", "api.anthropic"),
                      "Spec-first development with test generation from specs", "installed"),
        SkillBinding("mutating-skill", ("api.deepseek",),
                      "Mutation-test verification gates for catch-rate scoring", "installed"),
    ],
    "security_audit": [
        SkillBinding("ship-gate", ("api.deepseek", "api.anthropic"),
                      "Pre-production security/database/deployment audit", "installed"),
        SkillBinding("env-secrets-manager", ("local.slice",),
                      "Environment-variable hygiene and secrets safety auditing", "installed"),
        SkillBinding("sovereign-verification", ("api.deepseek", "api.anthropic"),
                      "Sovereign verification ledger and temporal epistemic graph", "installed"),
    ],
    "security_hardening": [
        SkillBinding("sovereign-ghl-infrastructure", ("api.deepseek", "api.anthropic"),
                      "Compliance, DNS/email, snapshot schemas, AI guardrails", "installed"),
    ],
    "observability": [
        SkillBinding("srse-calibrating-confidence", ("api.deepseek", "api.anthropic"),
                      "Confidence-level calibration with uncertainty tagging", "installed"),
    ],
    "ci_cd_pipeline": [
        SkillBinding("agent-harness", ("api.deepseek", "api.anthropic", "dsh.headless"),
                      "Bounded agentic loop with verify/retry/escalate", "installed"),
    ],
    "health_monitoring": [
        SkillBinding("sovereign-project-lifecycle-orchestrator", ("api.deepseek", "api.anthropic"),
                      "Health scorecard with evidence tiers", "installed"),
    ],
    "incident_response": [
        SkillBinding("ship-gate", ("api.deepseek", "api.anthropic"),
                      "Pre-production gate — blocks deploy until critical items pass", "installed"),
    ],
    "backup_recovery": [
        SkillBinding("sovereign-verification", ("api.deepseek", "api.anthropic"),
                      "Evidence anchoring and verification for restore integrity", "installed"),
    ],
    "audit_logging": [
        SkillBinding("sovereign-verification", ("api.deepseek", "api.anthropic"),
                      "Claim-evidence-freshness-verdict audit system", "installed"),
    ],
    "research_synthesis": [
        SkillBinding("srse-synthesizing-cross-domain", ("api.deepseek", "api.anthropic"),
                      "Cross-domain idea synthesis and novelty checking", "installed"),
        SkillBinding("srse-forecasting-scenarios", ("api.deepseek", "api.anthropic"),
                      "Trend/technology/market forecasting with honest uncertainty", "installed"),
        SkillBinding("notebooklm", ("api.deepseek",),
                      "NotebookLM — create notebooks, add sources, generate artifacts", "installed"),
    ],
    "automation_development": [
        SkillBinding("n8n-subworkflows", ("api.deepseek", "api.anthropic"),
                      "Reusable composable n8n sub-workflows", "installed"),
        SkillBinding("n8n-workflow-patterns", ("api.deepseek", "api.anthropic"),
                      "Proven workflow architectural patterns for n8n", "installed"),
    ],
    "browser_automation": [
        SkillBinding("ego-browser", ("dsh.headless", "api.deepseek"),
                      "Chromium-based browser for AI agent automation", "installed"),
    ],
    "integration_testing": [
        SkillBinding("n8n-multi-instance", ("api.deepseek", "api.anthropic"),
                      "Multi-instance n8n testing with instance targeting", "installed"),
        SkillBinding("system-connector", ("api.deepseek", "api.anthropic"),
                      "Deterministic connector to any third-party system/API", "installed"),
    ],
    "adversarial_testing": [
        SkillBinding("srse-validating-adversarially", ("api.deepseek", "api.anthropic"),
                      "Adversarial validation — fact-check claims before decisions", "installed"),
        SkillBinding("srse-inverting-assumptions", ("api.deepseek", "api.anthropic"),
                      "Axiom Inversion Logic — strongest case for the opposite", "installed"),
    ],
    "performance_testing": [
        SkillBinding("srse-designing-experiments", ("api.deepseek", "api.anthropic"),
                      "Experiment design with failure criteria and decision rules", "installed"),
    ],
    "failure_testing": [
        SkillBinding("srse-analyzing-implementations", ("api.deepseek", "api.anthropic"),
                      "Implementation analysis for failure modes", "installed"),
    ],
    "documentation": [
        SkillBinding("sovereign-project-lifecycle-orchestrator", ("api.deepseek", "api.anthropic"),
                      "Project map, health scorecard, evidence documentation", "installed"),
    ],
    "capability_planning": [
        SkillBinding("interchangeable-components", ("api.deepseek", "api.anthropic"),
                      "Capability seam blueprint — plan new swappable components", "installed"),
        SkillBinding("loop-library", ("api.deepseek", "api.anthropic"),
                      "Discover, audit, and design repeatable AI-agent loops", "installed"),
    ],
    "roadmap_planning": [
        SkillBinding("srse-forecasting-scenarios", ("api.deepseek", "api.anthropic"),
                      "Forecast scenarios for roadmap evaluation", "installed"),
        SkillBinding("srse-generating-frameworks", ("api.deepseek", "api.anthropic"),
                      "Framework generation with honest weakness enumeration", "installed"),
    ],
    "memory_management": [
        SkillBinding("workspace-memory", ("local.slice",),
                      "Silent memory secretary for workspace recall", "installed"),
        SkillBinding("freebuff-pzs-memory", ("local.slice",),
                      "Permanent memory protocol for vault ~/FREEBUFF_PZS", "installed"),
    ],
    "vault_search": [
        SkillBinding("vault-search", ("local.slice",),
                      "Semantic search over Obsidian vault via Qdrant", "installed"),
        SkillBinding("vault-check-first", ("local.slice",),
                      "Check vault before starting business/client work", "installed"),
    ],
    "mcp_development": [
        SkillBinding("mcp-server-builder", ("api.deepseek", "api.anthropic"),
                      "Design and ship MCP servers from OpenAPI contracts", "installed"),
    ],
    "process_design": [
        SkillBinding("process-mapper", ("api.deepseek", "api.anthropic"),
                      "BPMN-style process maps with cycle-time analysis", "installed"),
    ],
    "n8n_expertise": [
        SkillBinding("n8n-binary-and-data", ("api.deepseek", "api.anthropic"),
                      "Handle files and binary data in n8n correctly", "installed"),
        SkillBinding("n8n-code-javascript", ("api.deepseek", "api.anthropic"),
                      "JavaScript Code nodes in n8n — patterns and performance", "installed"),
        SkillBinding("n8n-code-python", ("api.deepseek", "api.anthropic"),
                      "Python Code nodes in n8n", "installed"),
        SkillBinding("n8n-agents", ("api.deepseek", "api.anthropic"),
                      "Design n8n AI agents — tool calling, memory, RAG, human review", "installed"),
        SkillBinding("n8n-error-handling", ("api.deepseek", "api.anthropic"),
                      "Wire n8n error handling — retries, Error Trigger, 4xx/5xx", "installed"),
        SkillBinding("n8n-expression-syntax", ("api.deepseek", "api.anthropic"),
                      "Validate n8n expression syntax and fix common errors", "installed"),
        SkillBinding("n8n-node-configuration", ("api.deepseek", "api.anthropic"),
                      "Operation-aware node configuration and property dependencies", "installed"),
        SkillBinding("n8n-validation-expert", ("api.deepseek", "api.anthropic"),
                      "Interpret n8n validation errors and guide fixes", "installed"),
        SkillBinding("n8n-self-hosting", ("dsh.headless",),
                      "Deploy production self-hosted n8n end-to-end to Linux VM", "installed"),
        SkillBinding("n8n-code-tool", ("api.deepseek", "api.anthropic"),
                      "Custom Code Tool for n8n AI agents — JS/Python sandbox", "installed"),
        SkillBinding("n8n-mcp-tools-expert", ("api.deepseek", "api.anthropic"),
                      "Expert guide for n8n-mcp MCP tools — search, validate, manage", "installed"),
    ],
}

# --- STAGE → ROLES ---

STAGE_ROLES: dict[str, list[Role]] = {
    "IDEA": [
        Role("Founder", "product", "Defines mission, problem, and initial scope", ("IDEA", "DISCOVERY")),
    ],
    "DISCOVERY": [
        Role("Product Researcher", "product", "User research, competitive analysis, problem validation",
             ("DISCOVERY", "RESEARCH")),
    ],
    "RESEARCH": [
        Role("Research Engineer", "research", "Literature review, feasibility studies, benchmarks",
             ("RESEARCH", "PROTOTYPE")),
    ],
    "ARCHITECTURE": [
        Role("Principal Architect", "engineering", "System design, component decomposition, interface contracts",
             ("ARCHITECTURE", "SPECIFICATION")),
        Role("Security Architect", "security", "Threat model, security boundaries, trust model",
             ("ARCHITECTURE", "SPECIFICATION")),
    ],
    "IMPLEMENTATION": [
        Role("Senior Engineer", "engineering", "Feature implementation, code review, test writing",
             ("IMPLEMENTATION", "INTEGRATION", "VERIFICATION")),
    ],
    "INTEGRATION": [
        Role("Integration Engineer", "engineering", "CI/CD, component wiring, contract testing",
             ("INTEGRATION", "VERIFICATION")),
    ],
    "VERIFICATION": [
        Role("QA Engineer", "engineering", "Test automation, adversarial testing, benchmark evaluation",
             ("VERIFICATION", "HARDENING")),
        Role("Security Auditor", "security", "Security audit, penetration testing, compliance check",
             ("VERIFICATION", "HARDENING")),
    ],
    "HARDENING": [
        Role("SRE", "ops", "Failure testing, chaos engineering, recovery validation, observability setup",
             ("HARDENING", "RELEASE", "OPERATIONS")),
        Role("Security Engineer", "security", "Hardening, threat modeling, least-privilege enforcement",
             ("HARDENING", "RELEASE")),
    ],
    "RELEASE": [
        Role("Release Manager", "ops", "Release automation, rollback strategy, user documentation",
             ("RELEASE", "OPERATIONS")),
    ],
    "OPERATIONS": [
        Role("SRE", "ops", "Health monitoring, incident response, backup/recovery, capacity planning",
             ("OPERATIONS", "OPTIMIZATION")),
        Role("Security Engineer", "security", "Patching, vulnerability management, audit log review",
             ("OPERATIONS", "OPTIMIZATION")),
    ],
    "OPTIMIZATION": [
        Role("Performance Engineer", "engineering", "Profiling, bottleneck analysis, cost optimization",
             ("OPTIMIZATION", "EVOLUTION")),
    ],
    "EVOLUTION": [
        Role("Principal Architect", "engineering", "Roadmap planning, technology assessment, migration strategy",
             ("EVOLUTION",)),
    ],
}


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def capabilities_for_stage(stage: str) -> list[str]:
    """Capabilities required at a given lifecycle stage."""
    return STAGE_CAPABILITIES.get(stage, [])


def skills_for_capability(capability_name: str) -> list[SkillBinding]:
    """Skills that provide a given capability."""
    return CAPABILITY_SKILLS.get(capability_name, [])


def roles_for_stage(stage: str) -> list[Role]:
    """Roles required at a given lifecycle stage."""
    return STAGE_ROLES.get(stage, [])


def provider_ids_for_capability(capability_name: str) -> list[str]:
    """All unique provider IDs that can provide a given capability."""
    ids: set[str] = set()
    for skill in skills_for_capability(capability_name):
        ids.update(skill.provider_ids)
    return sorted(ids)


def all_capabilities() -> list[str]:
    """Every capability defined in the graph."""
    return sorted(CAPABILITY_SKILLS.keys())


def capability_by_name(name: str) -> Capability | None:
    """Look up a capability definition by name."""
    for _cat_list in STAGE_CAPABILITIES.values():
        pass  # STAGE_CAPABILITIES values are just names
    # Build from the graph
    for cap_name, skills in CAPABILITY_SKILLS.items():
        if cap_name == name:
            return Capability(
                name=name,
                category=_infer_category(name),
                description=skills[0].description if skills else "",
                criticality=_infer_criticality(name),
                required_by_stages=tuple(
                    s for s, caps in STAGE_CAPABILITIES.items() if name in caps
                ),
            )
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _infer_category(name: str) -> str:
    cats = {
        "security": ["security_audit", "security_hardening", "security_model"],
        "ops": ["health_monitoring", "incident_response", "backup_recovery", "audit_logging",
                "ci_cd_pipeline", "release_management", "deployment_automation"],
        "research": ["problem_analysis", "literature_review", "benchmark_evaluation",
                     "feasibility_study", "research_synthesis"],
        "product": ["mission_definition", "user_research", "competitive_analysis", "roadmap_planning"],
        "engineering": ["architecture_design", "component_decomposition", "code_generation",
                        "code_review", "testing", "integration_testing", "adversarial_testing",
                        "failure_testing", "performance_testing", "documentation",
                        "component_wiring", "contract_testing"],
    }
    for cat, names in cats.items():
        if name in names:
            return cat
    return "engineering"


def _infer_criticality(name: str) -> int:
    critical = {
        "architecture_design": 9, "security_audit": 9, "code_generation": 8,
        "testing": 8, "health_monitoring": 8, "backup_recovery": 8,
        "incident_response": 9, "security_hardening": 9, "failure_testing": 7,
        "adversarial_testing": 7, "audit_logging": 7, "observability": 6,
        "mission_definition": 5, "problem_analysis": 5,
    }
    return critical.get(name, 5)


def graph_summary(stage: str) -> dict[str, Any]:
    """Full capability picture for a lifecycle stage."""
    caps = capabilities_for_stage(stage)
    capability_map: dict[str, list[dict[str, Any]]] = {}
    for cap in caps:
        skills = skills_for_capability(cap)
        capability_map[cap] = [
            {
                "skill": s.skill_name,
                "providers": list(s.provider_ids),
                "installation": s.installation,
            }
            for s in skills
        ]
    return {
        "stage": stage,
        "required_capabilities": caps,
        "capability_skills": capability_map,
        "required_roles": [
            {"name": r.name, "discipline": r.discipline, "description": r.description}
            for r in roles_for_stage(stage)
        ],
    }