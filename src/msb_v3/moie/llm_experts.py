"""LLM-backed MoIE experts + a diverse reviewer panel (completion blueprint).

The deterministic ``DomainExpert`` (keyword rules) is the default reviewer,
but it cannot *read* a diff — it inverts assumptions and checks blast radius
without understanding the code. ``LLMExpert`` fills that gap: it implements
the same ``Expert`` interface (so the controller, router and meta-critic are
unchanged) but calls a real model behind an injectable client.

The important part is the **model-diversity invariant**, enforced by
``ReviewPanel`` at construction time:

    * builder model ∉ reviewer models  (the builder never reviews itself),
    * reviewer models are pairwise distinct (no correlated blind spot).

Each ``LLMExpert`` is fail-closed: an unreachable model or unparseable
output becomes a CONCERN verdict with a clear summary — a panel whose
models are down can never APPROVE. Models are *recorded* on the report
(``ExpertReport.model``) so the factory's review carries "who reviewed with
which model" into the evidence chain.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from itertools import cycle
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from msb_v3.moie.experts import Expert
from msb_v3.moie.models import Assumption, ExpertReport

# Verdict line contract: the model must answer on its own line. Anything
# else (prose, JSON, no verdict) is treated as CONCERN — fail-closed.
_VERDICT_RE = re.compile(r"^\s*verdict\s*[:=\-]\s*(safe|concern|block)\b", re.IGNORECASE)

_OUTPUT_CONTRACT = (
    "You are an independent, adversarial code reviewer. Try to refute the "
    "change; do not seek agreement.\n"
    "Reply in exactly this line format (no markdown, no extra prose):\n"
    "VERDICT: SAFE|CONCERN|BLOCK\n"
    "RISK: <one specific risk, or omit>\n"
    "MITIGATION: <one specific mitigation, or omit>\n"
    "ASSUMPTION: <one assumption the change relies on, or omit>\n"
    "Repeat RISK/MITIGATION/ASSUMPTION lines as needed. BLOCK = a hard "
    "safety/correctness defect. CONCERN = must be fixed or explicitly "
    "mitigated. SAFE = no material defect found."
)

# Default lenses cycled across reviewer models: security first (the safety
# floor), then correctness (the semantic-review gap the deterministic rules
# cannot close), then maintainability.
DEFAULT_LENSES: Tuple[Tuple[str, str, str], ...] = (
    (
        "security",
        "Security Reviewer",
        "refute the change for security and safety defects (auth, secrets, "
        "injection, privilege, exposure, fail-closed behavior)",
    ),
    (
        "correctness",
        "Correctness Reviewer",
        "refute the change for logic and correctness defects (wrong behavior, "
        "edge cases, race conditions, broken invariants, tests that do not "
        "prove what they claim)",
    ),
    (
        "maintainability",
        "Maintainability Reviewer",
        "refute the change for structural defects (coupling, duplicated logic, "
        "schema/interface drift, undocumented assumptions, dead code)",
    ),
)

_VERDICT_CONFIDENCE = {"SAFE": 0.6, "CONCERN": 0.7, "BLOCK": 0.9}


def _after_colon(line: str) -> str:
    for sep in (":", "=", "-"):
        if sep in line:
            return line.split(sep, 1)[1].strip()
    return ""


def parse_expert_output(text: str) -> Tuple[str, List[str], List[str], List[str], bool]:
    """Parse a reviewer's line-oriented output.

    Returns ``(verdict, risks, mitigations, assumptions, explicit)`` where
    ``explicit`` is False when no verdict line was found (caller must
    fail-closed). The verdict defaults to CONCERN.
    """
    verdict = "CONCERN"
    explicit = False
    risks: List[str] = []
    mitigations: List[str] = []
    assumptions: List[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = _VERDICT_RE.match(s)
        if m:
            verdict = m.group(1).upper()
            explicit = True
            continue
        low = s.lower()
        for prefix, bucket in (("risk", risks), ("mitigation", mitigations), ("assumption", assumptions)):
            if low.startswith(prefix):
                val = _after_colon(s)
                if val:
                    bucket.append(val)
                break
    return verdict, risks, mitigations, assumptions, explicit


class LLMExpert(Expert):
    """One model-backed reviewer behind the ``Expert`` interface.

    ``model`` is the reviewer's model identity (the thing the diversity
    invariant checks). ``client`` / ``client_factory`` supply a
    ``generate(prompt, *, system=...) -> {text, model}`` object; default
    factory serves the configured local/frontier seam per model id.
    """

    def __init__(
        self,
        *,
        expert_id: str,
        name: str,
        model: str,
        description: str = "",
        lens: str = "",
        focus_keywords: Tuple[str, ...] = (),
        always_on: bool = False,
        client: Any = None,
        client_factory: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self.expert_id = expert_id
        self.name = name
        self.description = description
        self.lens = lens
        self.model = model
        self.focus_keywords = focus_keywords
        self.always_on = always_on
        self._client = client
        self._client_factory = client_factory

    def _resolve_client(self) -> Any:
        if self._client is not None:
            return self._client
        factory = self._client_factory or _default_client_factory
        return factory(self.model)

    def _system_prompt(self) -> str:
        return f"Lens: {self.name} — {self.description}\n\n{_OUTPUT_CONTRACT}"

    def _build_prompt(self, claim: str, context: Dict[str, Any]) -> str:
        parts = [f"CLAIM / CHANGE:\n{claim}"]
        changed = context.get("changed_files")
        if changed:
            parts.append("changed files: " + ", ".join(list(changed)[:10]))
        diff = context.get("diff")
        if diff:
            parts.append("diff (bounded):\n" + str(diff)[:2000])
        return "\n\n".join(parts)

    def analyze(self, claim: str, context: Optional[Dict[str, Any]] = None) -> ExpertReport:
        context = context or {}
        client = self._resolve_client()
        try:
            resp = client.generate(self._build_prompt(claim, context), system=self._system_prompt())
            text = (getattr(resp, "text", "") or "").strip()
        except Exception as exc:  # noqa: BLE001 — fail-closed with evidence
            return self._report(
                verdict="CONCERN",
                confidence=0.4,
                risks=[f"reviewer {self.model!r} unavailable: {type(exc).__name__}"],
                mitigations=[],
                assumptions=[],
                summary=f"{self.name} ({self.model}) unreachable — fail-closed to CONCERN",
            )

        verdict, risks, mitigations, assumption_texts, explicit = parse_expert_output(text)
        assumptions = [self._assumption(a, risks) for a in assumption_texts]
        if not explicit:
            return self._report(
                verdict="CONCERN",
                confidence=0.4,
                risks=risks,
                mitigations=mitigations,
                assumptions=assumptions,
                summary=f"{self.name} ({self.model}) returned no parseable verdict — fail-closed to CONCERN",
            )
        return self._report(
            verdict=verdict,
            confidence=_VERDICT_CONFIDENCE[verdict],
            risks=risks,
            mitigations=mitigations,
            assumptions=assumptions,
            summary=f"{self.name} ({self.model}): {verdict} ({len(risks)} risk(s), {len(assumptions)} assumption(s))",
        )

    def _assumption(self, text: str, risks: List[str]) -> Assumption:
        risk = risks[0] if risks else "the plan's premise changes materially"
        return Assumption(
            text=text[:200],
            kind="explicit",
            source=self.expert_id,
            confidence=0.5,
            inverted=f"if this assumption is wrong, {risk}",
            risk=risk,
        )

    def _report(
        self,
        *,
        verdict: str,
        confidence: float,
        risks: List[str],
        mitigations: List[str],
        assumptions: List[Assumption],
        summary: str,
    ) -> ExpertReport:
        return ExpertReport(
            expert_id=self.expert_id,
            expert_name=f"{self.name} ({self.model})",
            verdict=verdict,
            confidence=confidence,
            assumptions=assumptions,
            risks=list(dict.fromkeys(risks))[:5],
            mitigations=list(dict.fromkeys(mitigations))[:5],
            summary=summary,
            model=self.model,
        )


@dataclass(frozen=True)
class ReviewPanel:
    """N diverse LLM reviewers + the builder model they must not share.

    ``controller()`` turns the panel into a ``MoIEController`` whose registry
    is exactly these reviewers (always-on, so the router runs all of them).
    """

    builder_model: str
    experts: Tuple[LLMExpert, ...]

    def __post_init__(self) -> None:
        # Invariant enforced at construction: a ReviewPanel that violates
        # builder != reviewer (or pairwise-distinct models) cannot exist.
        self.validate()

    @property
    def reviewer_models(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(e.model for e in self.experts))

    def validate(self) -> None:
        if not self.experts:
            raise ValueError("review panel requires at least one reviewer")
        if any(e.model == self.builder_model for e in self.experts):
            raise ValueError(
                f"builder model {self.builder_model!r} may not also be a reviewer "
                f"(builder != reviewer invariant)"
            )
        if len(self.reviewer_models) != len(self.experts):
            raise ValueError(
                "reviewer models must be pairwise distinct (model-diversity invariant)"
            )

    def controller(self) -> Any:
        self.validate()
        from msb_v3.moie.engine import MoIEController
        from msb_v3.moie.experts import ExpertRegistry

        return MoIEController(registry=ExpertRegistry(experts=self.experts))


def _default_reviewer_models() -> List[str]:
    """Reviewer models from MSB_REVIEWER_MODELS (comma-separated), else the
    configured local model. Diversity is config-driven — the invariant is
    what guarantees it, not a hard-coded list of model names."""
    raw = os.getenv("MSB_REVIEWER_MODELS", "")
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    from msb_v3.core.config import settings

    return [settings.ollama_model]


def _default_client_factory(model: str) -> Any:
    from msb_v3.core.config import settings
    from msb_v3.local_ai.ollama import LocalAIClient

    if model == settings.openai_frontier_model and settings.openai_api_key:
        from msb_v3.fabric.model_router import FrontierClient

        return FrontierClient(model=model)
    return LocalAIClient(model=model)


def build_diverse_reviewer_panel(
    builder_model: str,
    *,
    models: Optional[Sequence[str]] = None,
    lenses: Optional[Sequence[Tuple[str, str, str]]] = None,
    client_factory: Optional[Callable[[str], Any]] = None,
) -> ReviewPanel:
    """Build a diverse reviewer panel, enforcing the builder!=reviewer and
    pairwise-distinct invariants (raises ValueError on violation).

    ``models`` maps 1:1 onto lenses (cycled); each (model, lens) becomes one
    ``LLMExpert``. Default models come from ``MSB_REVIEWER_MODELS``; default
    lenses are security/correctness/maintainability.
    """
    model_list = list(models) if models is not None else _default_reviewer_models()
    lens_list = list(lenses) if lenses is not None else list(DEFAULT_LENSES)
    if not model_list:
        raise ValueError("no reviewer models configured (set MSB_REVIEWER_MODELS)")
    experts = tuple(
        LLMExpert(
            expert_id=f"llm-{lens_id}",
            name=name,
            description=desc,
            lens=lens_id,
            model=model,
            always_on=True,
            client_factory=client_factory,
        )
        for model, (lens_id, name, desc) in zip(model_list, cycle(lens_list))
    )
    panel = ReviewPanel(builder_model=builder_model, experts=experts)
    panel.validate()
    return panel
