"""MoIE experts (spec §3, §31 item 18).

Ten deterministic experts, each a ``DomainExpert`` with its own domain
metadata: focus keywords (router selection), BLOCK-level danger keywords,
CONCERN-level keywords, risk templates, mitigation templates. The
interface is the ``Expert`` base — a future LLM-backed domain expert only
needs to implement ``analyze()``; the controller never depends on the
keyword machinery.

Safety floor: security, reliability and adversarial are always-on, so a
claim that mentions nothing domain-specific still gets inverted from the
three angles that matter most.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from msb_v3.moie.models import Assumption, ExpertReport
from msb_v3.moie.pipeline import (
    causal_alternatives,
    extract_assumptions,
    falsifiable_predictions,
    invert,
    keyword_hits,
)

VERDICT_SAFE = "SAFE"
VERDICT_CONCERN = "CONCERN"
VERDICT_BLOCK = "BLOCK"


class Expert:
    """One inversion expert. Implement ``analyze``; the controller only
    depends on this interface."""

    expert_id: str = ""
    name: str = ""
    description: str = ""
    focus_keywords: Tuple[str, ...] = ()
    always_on: bool = False

    def analyze(self, claim: str, context: Optional[Dict[str, Any]] = None) -> ExpertReport:
        raise NotImplementedError


class DomainExpert(Expert):
    """Deterministic keyword-driven expert over one domain.

    ``danger_keywords`` -> BLOCK verdict (a hard safety violation).
    ``concern_keywords`` -> CONCERN verdict. ``high_impact`` in context
    raises a CONCERN to BLOCK (the §25 gate leans fail-closed on
    consequential plans).

    Plain class (not a dataclass) so a subclass can author a custom expert
    the natural way — class attributes override the defaults, and are
    respected over constructor args:

        class MyDomain(DomainExpert):
            expert_id = "domain"
            focus_keywords = ("visa",)
            def analyze(self, claim, context=None): ...
    """

    def __init__(
        self,
        *,
        expert_id: str = "",
        name: str = "",
        description: str = "",
        focus_keywords: Tuple[str, ...] = (),
        always_on: bool = False,
        danger_keywords: Tuple[str, ...] = (),
        concern_keywords: Tuple[str, ...] = (),
        risk_templates: Optional[Dict[str, List[str]]] = None,
        mitigation_templates: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        cls = type(self)
        self.expert_id = cls.__dict__.get("expert_id", expert_id)
        self.name = cls.__dict__.get("name", name)
        self.description = cls.__dict__.get("description", description)
        self.focus_keywords = cls.__dict__.get("focus_keywords", focus_keywords)
        self.always_on = cls.__dict__.get("always_on", always_on)
        self.danger_keywords = cls.__dict__.get("danger_keywords", danger_keywords)
        self.concern_keywords = cls.__dict__.get("concern_keywords", concern_keywords)
        self.risk_templates = cls.__dict__.get("risk_templates", risk_templates or {})
        self.mitigation_templates = cls.__dict__.get("mitigation_templates", mitigation_templates or {})

    def analyze(self, claim: str, context: Optional[Dict[str, Any]] = None) -> ExpertReport:
        context = context or {}
        high_impact = bool(context.get("high_impact", False))
        danger = keyword_hits(claim, self.danger_keywords)
        concern = keyword_hits(claim, self.concern_keywords)

        # Verdict: fail-closed on danger; CONCERN on concern (or on
        # assumptions when the expert is always-on and the claim is
        # consequential); high-impact escalates CONCERN -> BLOCK.
        if danger:
            verdict = VERDICT_BLOCK
        elif concern:
            verdict = VERDICT_BLOCK if high_impact else VERDICT_CONCERN
        else:
            verdict = VERDICT_CONCERN if (high_impact and self.always_on) else VERDICT_SAFE

        # Assumptions: the shared extractor, annotated by this expert.
        assumptions: List[Assumption] = []
        for a in extract_assumptions(claim):
            assumptions.append(invert(a, source=self.expert_id, risk=self._risk_for(a.text, concern, danger)))

        risks: List[str] = []
        mitigations: List[str] = []
        for kw in danger:
            risks += self.risk_templates.get(kw, [f"{kw} present — treat as a hard blocker"])
            mitigations += self.mitigation_templates.get(kw, [f"remove or contain {kw!r} before proceeding"])
        for kw in concern:
            risks += self.risk_templates.get(kw, [f"{kw} needs scrutiny before this is safe"])
            mitigations += self.mitigation_templates.get(kw, [f"add explicit checks around {kw!r}"])
        if not risks and assumptions:
            risks.append(f"{len(assumptions)} unverified assumption(s) in the claim need confirmation")
            mitigations.append("verify each extracted assumption against independent evidence")

        confidence = min(0.95, 0.5 + 0.15 * len(danger) + 0.08 * len(concern))
        if not danger and not concern and not assumptions:
            confidence = 0.4  # little signal — say so honestly

        return ExpertReport(
            expert_id=self.expert_id,
            expert_name=self.name,
            verdict=verdict,
            confidence=confidence,
            assumptions=assumptions,
            risks=list(dict.fromkeys(risks))[:5],
            mitigations=list(dict.fromkeys(mitigations))[:5],
            falsifiable_predictions=falsifiable_predictions(list(dict.fromkeys(risks))[:4], claim),
            causal_alternatives=causal_alternatives(list(dict.fromkeys(risks))[:4]),
            summary=(
                f"{self.name}: {len(danger)} danger signal(s), {len(concern)} concern(s), "
                f"{len(assumptions)} assumption(s) inverted → {verdict}"
            ),
        )

    def _risk_for(self, text: str, concern: List[str], danger: List[str]) -> str:
        pool = danger or concern
        if pool:
            return f"if the assumption '{text[:120]}' is wrong, the {pool[0]} signal becomes a real exposure"
        return f"if the assumption '{text[:120]}' is wrong, the plan's premise changes materially"


# --- the ten experts ---------------------------------------------------------

SECURITY = DomainExpert(
    expert_id="security",
    name="Security Inversion Expert",
    description="Inverts safety assumptions: auth, secrets, injection, exposure.",
    focus_keywords=("auth", "password", "secret", "token", "key", "shell", "sudo", "network", "firewall", "encrypt", "injection", "expose", "privilege", "permission", "credential", "api key"),
    always_on=True,
    danger_keywords=("bypass", "disable auth", "privilege escalation", "shell", "eval(", "exec(", "chmod 777", "rm -rf", "sudo", "prompt injection", "sql injection", "0.0.0.0", "unauthenticated", "skip auth"),
    concern_keywords=("password", "secret", "token", "api key", "credential", "expose", "port", "firewall", "encrypt", "network"),
    risk_templates={
        "bypass": ["auth bypass: the plan assumes attackers cannot reach the bypassed control"],
        "shell": ["shell/exec surface: the plan assumes the executed command is always trusted"],
        "prompt injection": ["prompt injection: the plan assumes model output is never attacker-controlled"],
        "0.0.0.0": ["binding 0.0.0.0 exposes the service beyond the trusted network"],
        "unauthenticated": ["an unauthenticated path assumes no hostile caller can reach it"],
    },
    mitigation_templates={
        "bypass": ["remove the bypass path or gate it behind a second control"],
        "shell": ["restrict execution to a fixed allowlist; never interpolate untrusted input"],
        "prompt injection": ["treat model output as untrusted data; validate before acting"],
        "0.0.0.0": ["bind to loopback or a scoped interface"],
        "unauthenticated": ["require authentication on every reachable route"],
    },
)

RELIABILITY = DomainExpert(
    expert_id="reliability",
    name="Reliability Expert",
    description="Inverts availability assumptions: retries, timeouts, recovery.",
    focus_keywords=("retry", "timeout", "available", "failure", "crash", "recovery", "idempotent", "outage", "partial failure", "backup", "restore"),
    always_on=True,
    danger_keywords=(),
    concern_keywords=("retry", "timeout", "failure", "crash", "recovery", "idempotent", "outage", "backup", "restore", "partial"),
    risk_templates={
        "timeout": ["the plan assumes every call completes within the timeout"],
        "retry": ["the plan assumes retries are safe — a non-idempotent retry doubles effects"],
        "idempotent": ["the plan assumes the operation is idempotent without proving it"],
        "backup": ["the plan assumes backups exist and restore cleanly"],
    },
    mitigation_templates={
        "timeout": ["set explicit deadlines; classify timeout vs failure"],
        "retry": ["make retries idempotent or bound them with backoff + jitter"],
        "idempotent": ["prove idempotency with a test, not an assertion"],
        "backup": ["test the restore path, not just the backup path"],
    },
)

ADVERSARIAL = DomainExpert(
    expert_id="adversarial",
    name="Adversarial Expert",
    description="Asks who benefits from breaking this, and how.",
    focus_keywords=("attacker", "malicious", "untrusted", "spoof", "tamper", "replay", "csrf", "xss", "ssrf", "abuse", "fraud"),
    always_on=True,
    danger_keywords=("attacker", "malicious", "untrusted", "tamper", "replay", "csrf", "xss", "ssrf", "spoof"),
    concern_keywords=("abuse", "fraud", "spam", "impersonation", "social engineering"),
    risk_templates={
        "untrusted": ["the plan assumes untrusted input stays within its declared envelope"],
        "tamper": ["the plan assumes artifacts cannot be tampered with in transit or at rest"],
        "replay": ["the plan assumes a captured request cannot be replayed"],
        "abuse": ["the plan assumes the feature cannot be turned against its users"],
    },
    mitigation_templates={
        "untrusted": ["validate, normalize and bound every untrusted input"],
        "tamper": ["sign or hash artifacts; verify before use"],
        "replay": ["add freshness (nonce/timestamp) and reject repeats"],
        "abuse": ["add rate limits and abuse telemetry from day one"],
    },
)

ARCHITECTURE = DomainExpert(
    expert_id="architecture",
    name="Architecture Inversion Expert",
    description="Inverts structural assumptions: coupling, scale, interfaces.",
    focus_keywords=("monolith", "couple", "single point", "spof", "scale", "interface", "migration", "rewrite", "tech debt", "bottleneck", "eventual consistency", "race", "schema"),
    danger_keywords=(),
    concern_keywords=("monolith", "single point", "spof", "scale", "interface", "migration", "rewrite", "bottleneck", "race", "schema", "tech debt"),
    risk_templates={
        "single point": ["the plan assumes one component cannot become the single point of failure"],
        "migration": ["the plan assumes the migration is reversible and low-risk"],
        "rewrite": ["the plan assumes the rewrite preserves behavior the old system provably had"],
        "scale": ["the plan assumes the current architecture scales to the stated load"],
        "schema": ["the plan assumes schema changes are backward-compatible"],
    },
    mitigation_templates={
        "single point": ["identify and eliminate single points of failure"],
        "migration": ["design a rollback path and a canary before migrating"],
        "rewrite": ["pin current behavior with tests before rewriting"],
        "scale": ["load-test the stated target before committing"],
        "schema": ["make schema changes additive and versioned"],
    },
)

ECONOMIC = DomainExpert(
    expert_id="economic",
    name="Economic Inversion Expert",
    description="Inverts cost and incentive assumptions.",
    focus_keywords=("cost", "budget", "vendor", "license", "pricing", "subscription", "billing", "roi", "rate limit", "quota"),
    danger_keywords=(),
    concern_keywords=("cost", "budget", "vendor", "license", "pricing", "subscription", "billing", "quota", "rate limit"),
    risk_templates={
        "cost": ["the plan assumes the cost model holds at the stated scale"],
        "vendor": ["the plan assumes the vendor cannot change terms, pricing or availability"],
        "license": ["the plan assumes the license permits the intended use"],
        "rate limit": ["the plan assumes API rate limits will not bind"],
    },
    mitigation_templates={
        "cost": ["model cost at peak, not average; set a budget alert"],
        "vendor": ["treat vendor lock-in as a cost; plan an exit path"],
        "license": ["verify the license against the actual use case"],
        "rate limit": ["assume limits bind; build batching and backoff"],
    },
)

OPERATIONAL = DomainExpert(
    expert_id="operational",
    name="Operational Expert",
    description="Inverts deployment and operations assumptions.",
    focus_keywords=("deploy", "rollout", "migration", "downtime", "runbook", "oncall", "backup", "restore", "incident", "sla", "release"),
    danger_keywords=(),
    concern_keywords=("deploy", "rollout", "downtime", "runbook", "incident", "release", "sla"),
    risk_templates={
        "deploy": ["the plan assumes deployment is low-risk and reversible"],
        "downtime": ["the plan assumes downtime windows are acceptable or zero"],
        "runbook": ["the plan assumes an operator exists who knows how to respond"],
        "sla": ["the plan assumes the SLA is achievable with current tooling"],
    },
    mitigation_templates={
        "deploy": ["deploy in stages with automatic rollback on failure"],
        "downtime": ["design for zero-downtime or schedule the window explicitly"],
        "runbook": ["write and drill the runbook before the change goes live"],
        "sla": ["measure the current SLA before promising a new one"],
    },
)

GOVERNANCE = DomainExpert(
    expert_id="governance",
    name="Governance Expert",
    description="Inverts policy and approval assumptions.",
    focus_keywords=("policy", "compliance", "approval", "audit", "regulation", "consent", "review", "gdpr", "pii", "sox", "hipaa"),
    danger_keywords=("gdpr", "pii", "hipaa", "sox", "consent"),
    concern_keywords=("policy", "compliance", "approval", "audit", "regulation", "review"),
    risk_templates={
        "pii": ["the plan assumes PII can be handled without a data-protection review"],
        "consent": ["the plan assumes consent is already granted and can never be withdrawn"],
        "audit": ["the plan assumes the action is auditable end-to-end"],
        "approval": ["the plan assumes approval is implied by intent"],
    },
    mitigation_templates={
        "pii": ["route through the privacy review before handling PII"],
        "consent": ["record consent explicitly; honor revocation"],
        "audit": ["ensure every consequential step lands in the audit trail"],
        "approval": ["obtain explicit approval before the consequential step"],
    },
)

HUMAN_FACTOR = DomainExpert(
    expert_id="human-factor",
    name="Human-Factor Expert",
    description="Inverts assumptions about the humans operating the system.",
    focus_keywords=("user", "operator", "ux", "workflow", "training", "documentation", "fatigue", "alert", "dashboard", "manual"),
    danger_keywords=(),
    concern_keywords=("user", "operator", "manual", "training", "documentation", "alert", "fatigue"),
    risk_templates={
        "manual": ["the plan assumes a human will reliably perform a manual step every time"],
        "alert": ["the plan assumes alerts will be seen and acted on"],
        "training": ["the plan assumes operators are already trained on the new flow"],
        "documentation": ["the plan assumes the docs describe what was actually built"],
    },
    mitigation_templates={
        "manual": ["automate or gate the manual step with a checklist"],
        "alert": ["route alerts to the people who can act; drill the response"],
        "training": ["schedule training before the change lands"],
        "documentation": ["update docs as part of the change, not after"],
    },
)

DATA_MEMORY = DomainExpert(
    expert_id="data-memory",
    name="Data/Memory Expert",
    description="Inverts data, memory and provenance assumptions.",
    focus_keywords=("data", "privacy", "retention", "provenance", "memory", "embedding", "vector", "drift", "delete", "export", "cache"),
    danger_keywords=(),
    concern_keywords=("privacy", "retention", "provenance", "memory", "embedding", "drift", "delete", "cache", "export"),
    risk_templates={
        "privacy": ["the plan assumes stored data stays private forever"],
        "retention": ["the plan assumes retention limits are enforced, not documented"],
        "provenance": ["the plan assumes every memory/record carries its source"],
        "drift": ["the plan assumes embeddings/indexes do not drift from the source data"],
        "delete": ["the plan assumes delete actually deletes everywhere"],
        "cache": ["the plan assumes cached data cannot go stale"],
    },
    mitigation_templates={
        "privacy": ["classify data and encrypt at rest; scope access"],
        "retention": ["enforce retention with code, not policy documents"],
        "provenance": ["attach source/tenant/agent to every stored record"],
        "drift": ["re-index on a schedule and detect drift"],
        "delete": ["test that delete removes all copies"],
        "cache": ["set explicit TTLs and invalidate on writes"],
    },
)

DOMAIN = DomainExpert(
    expert_id="domain",
    name="Domain Expert",
    description="Pluggable domain slot — add focus keywords or a custom analyze() to activate.",
    focus_keywords=(),
    danger_keywords=(),
    concern_keywords=(),
)

# The canonical set (registration order = router's stable tie-break).
BUILTIN_EXPERTS: Tuple[Expert, ...] = (
    SECURITY,
    RELIABILITY,
    ADVERSARIAL,
    ARCHITECTURE,
    ECONOMIC,
    OPERATIONAL,
    GOVERNANCE,
    HUMAN_FACTOR,
    DATA_MEMORY,
    DOMAIN,
)


class ExpertRegistry:
    """Registry of available experts. Injectable for tests and for future
    LLM-backed domain experts — ``register`` replaces by id."""

    def __init__(self, experts: Optional[Tuple[Expert, ...]] = None) -> None:
        self._by_id: Dict[str, Expert] = {}
        self._order: List[str] = []
        for e in (experts if experts is not None else BUILTIN_EXPERTS):
            self.register(e)

    def register(self, expert: Expert) -> None:
        if expert.expert_id not in self._by_id:
            self._order.append(expert.expert_id)
        self._by_id[expert.expert_id] = expert

    def get(self, expert_id: str) -> Optional[Expert]:
        return self._by_id.get(expert_id)

    def list_order(self) -> List[Expert]:
        """Experts in registration order (deterministic router tie-break)."""
        return [self._by_id[eid] for eid in self._order if eid in self._by_id]

    def list(self) -> List[Dict[str, Any]]:
        return [
            {
                "expert_id": e.expert_id,
                "name": e.name,
                "description": e.description,
                "always_on": e.always_on,
                "focus_keywords": list(e.focus_keywords),
            }
            for e in self._by_id.values()
        ]
