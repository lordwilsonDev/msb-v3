"""Provider Selection Intelligence — route tasks to the best provider.

Builds on the existing ProviderRegistry with empirical routing:

    1. Per-provider performance profiles (success rate, latency, cost)
    2. Task → provider matching (capabilities, risk tier, privacy)
    3. Stage-aware routing (different lifecycle stages need different providers)
    4. Fallback chains (if primary fails, try next)

This is the bridge between PLEI's decision engine and MSB's provider seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProviderProfile:
    """Empirical profile for one provider."""

    provider_id: str
    display_name: str
    kind: str
    capabilities: list[str] = field(default_factory=list)
    max_risk_tier: int = 4
    available: bool = False
    # Empirical (from evidence — Phase 7 calibration populates these)
    success_rate: float = 0.90
    avg_latency_s: float = 5.0
    est_cost_per_task: float = 0.0  # approximate
    task_specialization: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProviderSelection:
    """Result of provider selection — who should run what."""

    task_description: str
    required_capabilities: list[str] = field(default_factory=list)
    max_risk_tier: int = 4
    primary: ProviderProfile | None = None
    fallbacks: list[ProviderProfile] = field(default_factory=list)
    rationale: str = ""


@dataclass(slots=True)
class ProviderReport:
    """Full provider intelligence report."""

    profiles: list[ProviderProfile] = field(default_factory=list)
    available_count: int = 0
    total_count: int = 0
    selections: list[ProviderSelection] = field(default_factory=list)


def build_profiles() -> list[ProviderProfile]:
    """Build provider profiles from the live ProviderRegistry."""
    try:
        from msb_v3.agent.providers import ProviderRegistry

        reg = ProviderRegistry()
        available_set = {p.spec.provider_id for p in reg.select(available_only=True)}
    except ImportError:
        # In tests without the full MSB runtime
        return _stub_profiles()

    profiles: list[ProviderProfile] = []
    for p in reg._providers:
        spec = p.spec
        available = spec.provider_id in available_set

        # Estimate capabilities and specialization from provider kind
        capabilities = list(spec.capabilities)
        specialization = _infer_specialization(spec.kind, capabilities)

        profiles.append(ProviderProfile(
            provider_id=spec.provider_id,
            display_name=spec.display_name,
            kind=spec.kind,
            capabilities=capabilities,
            max_risk_tier=spec.max_risk_tier,
            available=available,
            success_rate=_default_success_rate(spec.kind),
            avg_latency_s=_default_latency(spec.kind),
            est_cost_per_task=_default_cost(spec.kind),
            task_specialization=specialization,
        ))

    return profiles


def select_provider_for_task(
    task_description: str,
    required_capabilities: list[str],
    max_risk_tier: int = 4,
    profiles: list[ProviderProfile] | None = None,
) -> ProviderSelection:
    """Select the best provider for a task.

    Ranking criteria (in order):
      1. Capabilities match (must satisfy all required)
      2. Risk tier (must be at or below max)
      3. Availability (must be reachable)
      4. Specialization match (task type alignment)
      5. Success rate (higher is better)
    """
    if profiles is None:
        profiles = build_profiles()

    candidates: list[tuple[float, ProviderProfile]] = []

    required_set = set(required_capabilities)
    for p in profiles:
        if not p.available:
            continue
        if p.max_risk_tier > max_risk_tier:
            continue

        caps_set = set(p.capabilities)
        cap_match = len(required_set & caps_set) / max(1, len(required_set))

        if cap_match < 0.5 and required_set - caps_set:
            continue  # doesn't have critical capabilities

        # Score: capability match (50%) + success rate (30%) + specialization (20%)
        spec_match = 0.5 if any(s.lower() in task_description.lower() for s in p.task_specialization) else 0.0
        score = (cap_match * 0.50) + (p.success_rate * 0.30) + (spec_match * 0.20)

        candidates.append((score, p))

    candidates.sort(key=lambda x: -x[0])

    primary = candidates[0][1] if candidates else None
    fallbacks = [c[1] for c in candidates[1:4]]

    rationale = _build_rationale(primary, fallbacks, required_capabilities, candidates)

    return ProviderSelection(
        task_description=task_description,
        required_capabilities=required_capabilities,
        max_risk_tier=max_risk_tier,
        primary=primary,
        fallbacks=fallbacks,
        rationale=rationale,
    )


def _build_rationale(
    primary: ProviderProfile | None,
    fallbacks: list[ProviderProfile],
    required_caps: list[str],
    candidates: list[tuple[float, ProviderProfile]],
) -> str:
    if not candidates:
        return (
            f"No available provider for capabilities: {required_caps}. "
            f"Check provider registration and API key configuration."
        )
    if primary is None:
        return "No suitable primary provider — escalate to human."
    return (
        f"Selected {primary.display_name} ({primary.kind}) — "
        f"covers {_cap_overlap(primary.capabilities, required_caps)}, "
        f"success rate {primary.success_rate:.0%}, "
        f"{'no' if not fallbacks else len(fallbacks)} fallback(s) available"
    )


def _cap_overlap(provider_caps: list[str], required: list[str]) -> str:
    matched = [c for c in required if c in provider_caps]
    return f"{len(matched)}/{len(required)} capabilities"


# ---------------------------------------------------------------------------
# Default estimates — Phase 7 calibration will replace these with real data
# ---------------------------------------------------------------------------


def _default_success_rate(kind: str) -> float:
    return {
        "local": 0.95,
        "api": 0.92,
        "cli": 0.85,
        "dsh": 0.88,
        "paseo": 0.90,
    }.get(kind, 0.90)


def _default_latency(kind: str) -> float:
    return {
        "local": 3.0,
        "api": 2.0,
        "cli": 8.0,
        "dsh": 6.0,
        "paseo": 4.0,
    }.get(kind, 5.0)


def _default_cost(kind: str) -> float:
    return {
        "local": 0.0,
        "api": 0.01,
        "cli": 0.0,
        "dsh": 0.0,
        "paseo": 0.0,
    }.get(kind, 0.0)


def _infer_specialization(kind: str, capabilities: list[str]) -> list[str]:
    """Infer task specialization from provider kind."""
    by_kind: dict[str, list[str]] = {
        "local": ["code generation", "documentation", "reasoning"],
        "api": ["code generation", "analysis", "translation"],
        "cli": ["execution", "testing", "build"],
        "dsh": ["agent loop", "tool orchestration", "research"],
        "paseo": ["planning", "architecture", "design"],
    }
    return by_kind.get(kind, ["general"])


def _stub_profiles() -> list[ProviderProfile]:
    """Stub profiles for testing without the runtime."""
    return [
        ProviderProfile(
            provider_id="api.deepseek",
            display_name="DeepSeek API",
            kind="api",
            capabilities=["code_generation", "analysis", "documentation"],
            max_risk_tier=3,
            available=True,
            success_rate=0.92,
            avg_latency_s=2.0,
        ),
        ProviderProfile(
            provider_id="local.slice",
            display_name="Local (Ollama/Qwen3)",
            kind="local",
            capabilities=["code_generation", "analysis", "documentation", "reasoning"],
            max_risk_tier=4,
            available=True,
            success_rate=0.95,
            avg_latency_s=3.0,
        ),
        ProviderProfile(
            provider_id="cli.codebuddy",
            display_name="CodeBuddy CLI",
            kind="cli",
            capabilities=["execution", "testing", "build", "code_generation"],
            max_risk_tier=4,
            available=True,
            success_rate=0.85,
            avg_latency_s=8.0,
        ),
        ProviderProfile(
            provider_id="dsh.headless",
            display_name="DeepSeek Harness",
            kind="dsh",
            capabilities=["code_generation", "analysis", "testing", "research"],
            max_risk_tier=4,
            available=False,
            success_rate=0.88,
            avg_latency_s=6.0,
        ),
    ]


def provider_report_as_dict(report: ProviderReport) -> dict[str, Any]:
    return {
        "profiles": [
            {
                "provider_id": p.provider_id,
                "display_name": p.display_name,
                "kind": p.kind,
                "capabilities": p.capabilities,
                "max_risk_tier": p.max_risk_tier,
                "available": p.available,
                "success_rate": p.success_rate,
                "avg_latency_s": p.avg_latency_s,
                "task_specialization": p.task_specialization,
            }
            for p in report.profiles
        ],
        "available_count": report.available_count,
        "total_count": report.total_count,
        "selections": [
            {
                "task": s.task_description,
                "required_capabilities": s.required_capabilities,
                "primary": {
                    "provider_id": s.primary.provider_id,
                    "display_name": s.primary.display_name,
                    "kind": s.primary.kind,
                    "success_rate": s.primary.success_rate,
                } if s.primary else None,
                "fallbacks": [
                    {"provider_id": f.provider_id, "display_name": f.display_name}
                    for f in s.fallbacks
                ],
                "rationale": s.rationale,
            }
            for s in report.selections
        ],
    }