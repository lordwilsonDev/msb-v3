"""Hybrid model router (spec §3.5 Router Decision, Phase 2 acceptance).

Decides which tier — local (Ollama/llama.cpp) or frontier (the /v1 seam) —
handles a given task, scored on the blueprint's R score:

    R = privacy + capability + latency + cost + hardware + confidence

The decision is *deterministic* for a given input (models propose, code
governs): tier defaults come from the task kind (frontier = plan /
verify-synth; local = classify / embed / route / routine tool call), then
the R score is computed and compared against a threshold. Every decision is
logged to Prometheus (bounded labels) and returned as a spec §3.5 Router
Decision.

Frontier availability is injectable and defaults to "the /v1 adapter is
configured" (OPENAI_API_KEY set). A caller may further probe reachability;
the router itself never makes a network call during decide() — it returns
the decision and the caller executes (or degrades, recording the reason).

    decision = ModelRouter().decide("plan", privacy_scoped=intent.privacy)
    client = frontier_client if decision.tier == "frontier" and decision.available else local
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

import httpx

from msb_v3.core.config import settings
from msb_v3.observability.metrics import Counter

# --- Observability (Phase 2: router decisions are visible) ---
ROUTER_DECISIONS = Counter(
    "msb_v3_router_decisions_total",
    "Model-router decisions",
    ["task_kind", "tier", "cause"],
)

# task_kind -> default tier. Frontier is reserved for the long-horizon brain
# (A5 fix: local is first-class for routine work, not for planning).
DEFAULT_TIER: Dict[str, str] = {
    "plan": "frontier",
    "verify_synth": "frontier",
    "classify": "local",
    "embed": "local",
    "route": "local",
    "routine_tool_call": "local",
    "chat": "local",
}

# R-score components and their relative weights (sums to 1.0). All component
# scores are 0..1 where higher = prefer frontier.
WEIGHTS: Dict[str, float] = {
    "privacy": 0.25,
    "capability": 0.25,
    "latency": 0.10,
    "cost": 0.15,
    "hardware": 0.10,
    "confidence": 0.15,
}

# Frontier preference once R exceeds this.
FRONTIER_THRESHOLD = 0.5

# Bounded causes for the metrics label (no free-form strings in labels).
_CAUSE_TIER_DEFAULT = "tier-default"
_CAUSE_PRIVACY = "privacy"
_CAUSE_CAPABILITY = "capability"
_CAUSE_UNAVAILABLE = "frontier-unavailable"


@dataclass(frozen=True)
class RouterDecision:
    task_kind: str
    tier: str  # "local" | "frontier"
    model: str
    reason: str
    score: float
    privacy_scoped: bool
    available: bool = True  # False = caller must degrade to local
    # Full component scores (higher = frontier preference), for debugging.
    components: Dict[str, float] = None  # type: ignore[assignment]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def frontier_available() -> bool:
    """True when the /v1 frontier seam is configured (OPENAI_API_KEY set).

    Reachability is deliberately not probed here — decide() is a pure,
    deterministic function. The caller owns execution; a failed frontier
    call degrades to local and records the reason (see degrade()).
    """
    return bool(settings.openai_api_key)


class ModelRouter:
    def __init__(
        self,
        *,
        weights: Optional[Dict[str, float]] = None,
        threshold: float = FRONTIER_THRESHOLD,
        available: Optional[bool] = None,
    ) -> None:
        self._weights = {**WEIGHTS, **(weights or {})}
        self._threshold = threshold
        # Injectable availability (tests pin it); None = read config live.
        self._available_override = available

    # -- public API -----------------------------------------------------

    def decide(
        self,
        task_kind: str,
        *,
        privacy_scoped: bool = False,
        hard_capability: bool = False,
        confidence_required: float = 0.0,
        latency_tolerance_s: Optional[float] = None,
    ) -> RouterDecision:
        """Route one task to local or frontier.

        Deterministic: same inputs -> same decision. No I/O. The score is
        the weighted R value (higher = frontier preference); tier comes from
        the default mapping unless the score overrides it.
        """
        components = self._components(
            task_kind,
            privacy_scoped=privacy_scoped,
            hard_capability=hard_capability,
            confidence_required=confidence_required,
            latency_tolerance_s=latency_tolerance_s,
        )
        score = round(sum(self._weights[k] * components[k] for k in WEIGHTS), 4)

        available = self._available_override if self._available_override is not None else frontier_available()
        default = DEFAULT_TIER.get(task_kind, "local")
        cause = _CAUSE_TIER_DEFAULT

        tier = default
        if score > self._threshold and available:
            tier = "frontier"
        elif default == "frontier":
            # A frontier-default task with a low score or no seam: degrade to
            # local with an honest reason — never silently fake the frontier.
            tier = "local"
            if not available:
                cause = _CAUSE_UNAVAILABLE

        if privacy_scoped and task_kind in ("plan", "verify_synth"):
            # Privacy is a hard floor for the brain too: never force frontier
            # on a privacy-scoped long-horizon task.
            tier = "local"
            cause = _CAUSE_PRIVACY
        elif hard_capability and default == "local":
            # A routine-looking task that actually needs frontier reasoning
            # (rare; the caller must justify it).
            tier = "frontier" if available else "local"
            cause = _CAUSE_CAPABILITY

        model = self._model_for(tier)
        reason = self._reason(task_kind, tier, cause, score, privacy_scoped, available)
        ROUTER_DECISIONS.labels(task_kind=task_kind, tier=tier, cause=cause).inc()

        return RouterDecision(
            task_kind=task_kind,
            tier=tier,
            model=model,
            reason=reason,
            score=score,
            privacy_scoped=privacy_scoped,
            available=available,
            components=components,
        )

    # -- internals ------------------------------------------------------

    def _components(
        self,
        task_kind: str,
        *,
        privacy_scoped: bool,
        hard_capability: bool,
        confidence_required: float,
        latency_tolerance_s: Optional[float],
    ) -> Dict[str, float]:
        """Per-component frontier-preference scores (0..1)."""
        privacy = 0.0 if privacy_scoped else 0.5  # scoped => local preference
        capability = 1.0 if hard_capability else (0.9 if task_kind in ("plan", "verify_synth") else 0.1)
        # Long-horizon tasks tolerate latency; interactive routine calls don't.
        if latency_tolerance_s is None:
            latency = 0.9 if task_kind in ("plan", "verify_synth") else 0.1
        else:
            latency = 0.9 if latency_tolerance_s >= 30.0 else 0.1
        cost = 0.6 if task_kind in ("plan", "verify_synth") else 0.2  # frontier premium is worth it for the brain
        hardware = 0.1  # no NPU detection in v1 (Phase 8) — neutral-low
        confidence = min(1.0, confidence_required) if confidence_required > 0 else (0.8 if task_kind in ("plan", "verify_synth") else 0.3)
        return {
            "privacy": privacy,
            "capability": capability,
            "latency": latency,
            "cost": cost,
            "hardware": hardware,
            "confidence": confidence,
        }

    def _model_for(self, tier: str) -> str:
        if tier == "frontier":
            return settings.openai_frontier_model
        return settings.ollama_model

    @staticmethod
    def _reason(
        task_kind: str,
        tier: str,
        cause: str,
        score: float,
        privacy_scoped: bool,
        available: bool,
    ) -> str:
        if cause == _CAUSE_PRIVACY:
            return f"{task_kind} is privacy-scoped — forced local (R={score})"
        if cause == _CAUSE_UNAVAILABLE:
            return f"{task_kind} defaults to frontier but the /v1 seam is closed — degraded to local (R={score})"
        if cause == _CAUSE_CAPABILITY:
            return f"{task_kind} requires frontier capability — routed {tier} (R={score})"
        if tier == "frontier":
            return f"{task_kind} prefers frontier for long-horizon work (R={score})"
        return f"{task_kind} is routine local work (R={score})"


class FrontierClient:
    """Minimal OpenAI-compatible client over the /v1 seam (same interface as
    the local clients: generate() -> LocalAIResponse). Lazy: only touches the
    network when called. On failure it raises so the caller can degrade to
    local and record the reason (Phase 2 acceptance: never fake the tier)."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: float = 120.0,
    ) -> None:
        self.base_url = (base_url or settings.openai_frontier_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.openai_api_key
        self.model = model or settings.openai_frontier_model
        self.timeout_s = timeout_s

    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        tools: Optional[list[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> Any:
        from msb_v3.local_ai.ollama import LocalAIResponse  # shared shape

        if not self.api_key:
            raise RuntimeError("frontier /v1 seam closed: OPENAI_API_KEY not set")

        messages: list[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools

        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise ConnectionError(f"frontier unreachable: {self.base_url} ({exc})") from exc

        latency = round(time.perf_counter() - t0, 4)
        msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
        text = msg.get("content", "") or ""
        usage = data.get("usage", {}) or {}
        return LocalAIResponse(
            text=text,
            model=self.model,
            latency_s=latency,
            tool_calls=msg.get("tool_calls") or [],
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        )


def resolve_client(
    task_kind: str,
    *,
    client: Optional[Any] = None,
    router: Optional[ModelRouter] = None,
    privacy_scoped: bool = False,
) -> tuple[Any, RouterDecision | None]:
    """Pick the client for a task through the router (Phase 2 wiring).

    An injected `client` wins (tests and callers that already resolved a
    client); otherwise the router decides and returns (client, decision).
    The decision is always returned so callers can record/log it even when
    a client was injected.

    Frontier decision + seam configured -> FrontierClient. Anything else
    (local tier, closed seam) -> the local factory client. The caller is
    responsible for executing; a frontier failure degrades per its own
    failure path (never silently faked).
    """
    if router is None:
        router = ModelRouter()
    decision = router.decide(task_kind, privacy_scoped=privacy_scoped)
    if client is not None:
        return client, decision
    if decision.tier == "frontier" and decision.available:
        return FrontierClient(), decision
    from msb_v3.local_ai.client_factory import get_client

    return get_client(), decision
