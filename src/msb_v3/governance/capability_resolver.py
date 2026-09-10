"""Deterministic capability resolver — V1.

Phase 3 deliverable.

Resolves a natural-language request into a capability decision *before* the
gate, so the system is not gating on a raw string. V1 is **deterministic**.
No LLM authority. No confidence theater. The MoIE keyword/pattern signal is
one input among several, not the security boundary.

Design intent:
- Confidence is descriptive, not authority. ``confidence=1.0`` means resolved
  by an explicit source. ``confidence=0.0`` means no match.
- The resolver returns ``UNKNOWN`` when no source resolves the request.
- The resolver does not gate. It proposes a capability; the gate and the
  policy engine authorize.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from msb_v3.governance.capability_registry import CapabilityRegistry
from msb_v3.governance.tool_manifest import ToolManifest, ToolManifestRegistry

# ---------------------------------------------------------------------------
# Output object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapabilityResolution:
    """One capability resolution.

    This is a description, not an authorization decision. ``confidence`` is
    descriptive only; it does not authorize execution.
    """

    resolved_capability: Optional[str]
    resolution_method: str
    confidence: float
    alternatives: Tuple[str, ...]
    ambiguous: bool
    risk_tier: Optional[int]
    reason: str
    evidence: Tuple[str, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "resolved_capability": self.resolved_capability,
            "resolution_method": self.resolution_method,
            "confidence": self.confidence,
            "alternatives": list(self.alternatives),
            "ambiguous": self.ambiguous,
            "risk_tier": self.risk_tier,
            "reason": self.reason,
            "evidence": list(self.evidence),
        }


# ---------------------------------------------------------------------------
# Deterministic intent templates
# ---------------------------------------------------------------------------

# Small, explicit, auditable set of request shapes -> capabilities.
# This is deliberately tiny in V1. If a request doesn't match a template, the
# resolver does not guess — it returns UNKNOWN.

_INTENT_TEMPLATES: Tuple[Tuple[Tuple[str, ...], str, str], ...] = (
    # (literal substring probes, resolved capability, reason)
    (("search the vault",), "read_vault", "request explicitly asks to search the vault"),
    (("read the vault",), "read_vault", "request explicitly asks to read the vault"),
    (("summarize", "synthesize", "write a brief", "write a note"), "llm_synthesis", "request asks for synthesis / drafting"),
    (("search the web", "web search", "look up online"), "web_search", "request asks for web search"),
    (("write a file", "write the file", "create a file"), "write_file", "request asks to write a local file"),
    (("delete", "remove", "wipe", "destroy", "nuke"), "vault_delete", "request asks for deletion / destruction of content"),
    (("send", "message", "notify", "email"), "send_message", "request asks to send a message / outbound communication"),
    (("financial", "payment", "transfer", "purchase"), "financial", "request asks for a financial capability"),
    (("permission", "access", "grant", "revoke"), "permissions", "request asks for a permissions capability"),
)


def _match_template(request: str) -> Optional[Tuple[str, str]]:
    """Return (capability, reason) for the first matching template, or None."""
    lower = request.lower()
    for probes, capability, reason in _INTENT_TEMPLATES:
        if any(probe in lower for probe in probes):
            return (capability, reason)
    return None


# ---------------------------------------------------------------------------
# Keyword/pattern signal from the existing MoIE templates
# ---------------------------------------------------------------------------

# The existing MoIE detection policy becomes one signal: a request that
# matches a MoIE BLOCK template is treated as a hint toward a dangerous
# capability. It does NOT resolve the capability by itself in V1 — the resolver
# still returns UNKNOWN unless a template or manifest matches. This keeps the
# keyword engine as evidence, not authority.

# Note: importing the live MoIEController here would make the resolver depend
# on the model path. For V1, we keep the resolver purely deterministic and do
# not call MoIE. If a caller wants the MoIE signal folded in, it can do so
# outside the resolver and pass it as evidence.


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class CapabilityResolver:
    """Deterministic capability resolver, V1.

    Resolution sources in priority order:
    1. Tool manifest — if the request names a known tool, the tool's declared
       capabilities are the strongest signal.
    2. Capability registry — if the request mentions a known capability by name.
    3. Deterministic intent templates — a small explicit set of request shapes.
    4. Otherwise UNKNOWN.
    """

    def __init__(
        self,
        capability_registry: Optional[CapabilityRegistry] = None,
        tool_manifest_registry: Optional[ToolManifestRegistry] = None,
    ) -> None:
        self._capability_registry = (
            capability_registry if capability_registry is not None else CapabilityRegistry()
        )
        self._tool_manifest_registry = (
            tool_manifest_registry
            if tool_manifest_registry is not None
            else ToolManifestRegistry(self._capability_registry)
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def resolve(self, request: str, *, tool_name: Optional[str] = None) -> CapabilityResolution:
        """Resolve a request into a capability decision.

        ``tool_name`` is optional metadata about which tool the caller expects
        to exercise. If provided and the tool has a manifest, the manifest is
        the primary signal.
        """
        evidence: List[str] = []
        alternatives: List[str] = []

        # 1. Tool manifest — strongest deterministic signal when available.
        if tool_name is not None:
            manifest = self._tool_manifest_registry.lookup(tool_name)
            if manifest is not None and manifest.may_exercise_any():
                evidence.append(f"tool manifest for {tool_name!r}")
                resolved = self._first_declared_capability(manifest)
                if resolved is not None:
                    registered = self._capability_registry.resolve(resolved)
                    if registered is not None:
                        return CapabilityResolution(
                            resolved_capability=resolved,
                            resolution_method="tool_manifest",
                            confidence=1.0,
                            alternatives=tuple(alternatives),
                            ambiguous=False,
                            risk_tier=registered.tier,
                            reason=f"tool {tool_name!r} declares capability {resolved!r}",
                            evidence=tuple(evidence),
                        )

        # 2. Capability registry — request mentions a known capability by name.
        named = self._extract_named_capability(request)
        if named is not None:
            cap = self._capability_registry.resolve(named)
            if cap is not None:
                evidence.append(f"capability name {named!r} in registry")
                return CapabilityResolution(
                    resolved_capability=cap.capability_id,
                    resolution_method="capability_name",
                    confidence=1.0,
                    alternatives=tuple(alternatives),
                    ambiguous=False,
                    risk_tier=cap.tier,
                    reason=f"request names registered capability {named!r}",
                    evidence=tuple(evidence),
                )
            alternatives.append(named)

        # 3. Deterministic intent templates.
        template_match = _match_template(request)
        if template_match is not None:
            capability, reason = template_match
            cap = self._capability_registry.resolve(capability)
            tier = cap.tier if cap is not None else None
            evidence.append(f"intent template matched: {reason}")
            return CapabilityResolution(
                resolved_capability=capability,
                resolution_method="intent_template",
                confidence=1.0,
                alternatives=tuple(alternatives),
                ambiguous=False,
                risk_tier=tier,
                reason=reason,
                evidence=tuple(evidence),
            )

        # 4. Otherwise UNKNOWN.
        evidence.append("no registered source resolved the request")
        return CapabilityResolution(
            resolved_capability=None,
            resolution_method="none",
            confidence=0.0,
            alternatives=tuple(alternatives),
            ambiguous=True,
            risk_tier=None,
            reason="no registered capability resolved this request",
            evidence=tuple(evidence),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _first_declared_capability(self, manifest: ToolManifest) -> Optional[str]:
        caps = manifest.capabilities
        if caps:
            return caps[0]
        return None

    def _extract_named_capability(self, request: str) -> Optional[str]:
        """Return the first known capability ID whose name appears in the
        request, or None.

        This is a weak signal — mentioning a capability by name does not mean
        the request will exercise it. It is used only as a hint toward
        resolution, and only when a template or manifest does not already
        resolve the request.
        """
        lower = request.lower()
        for capability_id in self._capability_registry.ids():
            if capability_id in lower:
                return capability_id
        return None


# ---------------------------------------------------------------------------
# Convenience: registry + manifest already wired
# ---------------------------------------------------------------------------

def default_resolver() -> CapabilityResolver:
    return CapabilityResolver()
