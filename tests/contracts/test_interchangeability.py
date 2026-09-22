"""Interchangeability spine (convergence blueprint §30).

Proves that two providers satisfying ProviderContract v1 can be swapped
without runtime code needing provider-specific assumptions. The test
verifies:

1. Structural interchangeability: both providers have the same contract
   shape (same fields, same types, same invariants).
2. Behavioral interchangeability: both providers return the same result
   shape from execute() — the caller doesn't need to know which one ran.
3. Registry interchangeability: ProviderRegistry.select() returns
   providers that are interchangeable within the same tier/capability
   constraints.

The acceptance condition:
    Provider A and Provider B can satisfy the same contract without
    runtime code needing provider-specific assumptions.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, fields, replace
from typing import Any, Dict, List, Optional, Tuple

import pytest

from msb_v3.agent.contract import (
    CONTRACT_VERSION,
    contract_from_spec,
    validate_contract,
)
from msb_v3.agent.providers import (
    AgentProvider,
    ProviderResult,
    default_providers,
)

# ---------------------------------------------------------------------------
# Provider pairs to test interchangeability
# ---------------------------------------------------------------------------

# Local + API providers that share the same capabilities and risk tier.
# These are the "can they swap?" pairs. The DeepSeek API provider was
# retired with the frontier seam (D1, 2026-09-09) — Anthropic remains.
_INTERCHANGEABLE_PAIRS: List[Tuple[str, str]] = [
    ("local.slice", "api.anthropic"),  # both: search_query, chat, vault_write; tier 3
]

# Both sides of that pair, for the behavioral tests below.
_INTERCHANGEABLE_IDS: Tuple[str, ...] = ("local.slice", "api.anthropic")

# The governed-loop surface EVERY provider must drive through handle() — this
# is the part a caller can rely on regardless of which provider ran.
_GOVERNED_HANDLE_KWARGS = frozenset(
    {"client", "spine", "session", "tenant", "approve", "output_dir"}
)

# Provider-specific extras a provider may legitimately add on top. local.slice
# injects its own DAG provider + ActionGate; api.anthropic relies on the
# defaults handle() already builds. Never a divergence in the contract.
_ALLOWED_EXTRA_HANDLE_KWARGS = frozenset({"provider", "gate"})

# CLI providers that share the same contract shape (no capabilities, tier 4).
_CLI_PAIRS: List[Tuple[str, str]] = [
    ("cli.claude", "cli.codex"),
    ("cli.claude", "cli.opencode"),
    ("cli.codex", "cli.opencode"),
]

# Paseo providers that share the same contract shape.
_PASEO_PAIRS: List[Tuple[str, str]] = [
    ("paseo.claude", "paseo.codex"),
    ("paseo.claude", "paseo.opencode"),
]


def _provider_map() -> Dict[str, AgentProvider]:
    return {p.spec.provider_id: p for p in default_providers()}


# ---------------------------------------------------------------------------
# Structural interchangeability
# ---------------------------------------------------------------------------


class TestStructuralInterchangeability:
    """Two providers that share capabilities and risk tier must have
    structurally identical contracts — same fields, same types, same
    invariants. This proves the caller can treat them identically."""

    @pytest.mark.parametrize("id_a,id_b", _INTERCHANGEABLE_PAIRS)
    def test_same_contract_version(self, id_a: str, id_b: str):
        """Both providers must conform to the same contract version."""
        providers = _provider_map()
        a, b = providers[id_a], providers[id_b]
        assert a.spec.contract_version == b.spec.contract_version == CONTRACT_VERSION

    @pytest.mark.parametrize("id_a,id_b", _INTERCHANGEABLE_PAIRS)
    def test_same_capabilities(self, id_a: str, id_b: str):
        """Interchangeable providers must declare the same capabilities."""
        providers = _provider_map()
        a, b = providers[id_a], providers[id_b]
        assert set(a.spec.capabilities) == set(b.spec.capabilities), (
            f"{id_a} capabilities={a.spec.capabilities} != "
            f"{id_b} capabilities={b.spec.capabilities}"
        )

    @pytest.mark.parametrize("id_a,id_b", _INTERCHANGEABLE_PAIRS)
    def test_same_risk_tier(self, id_a: str, id_b: str):
        """Interchangeable providers must have the same max risk tier."""
        providers = _provider_map()
        a, b = providers[id_a], providers[id_b]
        assert a.spec.max_risk_tier == b.spec.max_risk_tier, (
            f"{id_a} tier={a.spec.max_risk_tier} != "
            f"{id_b} tier={b.spec.max_risk_tier}"
        )

    @pytest.mark.parametrize("id_a,id_b", _INTERCHANGEABLE_PAIRS)
    def test_both_conform_to_contract(self, id_a: str, id_b: str):
        """Both providers must produce valid contracts from their specs."""
        providers = _provider_map()
        a, b = providers[id_a], providers[id_b]
        contract_a = contract_from_spec(a.spec)
        contract_b = contract_from_spec(b.spec)
        errors_a = validate_contract(contract_a)
        errors_b = validate_contract(contract_b)
        assert not errors_a, f"{id_a} contract errors: {errors_a}"
        assert not errors_b, f"{id_b} contract errors: {errors_b}"

    @pytest.mark.parametrize("id_a,id_b", _INTERCHANGEABLE_PAIRS)
    def test_same_kind_category(self, id_a: str, id_b: str):
        """Interchangeable providers must be in the same kind category
        (local/api providers are both 'local' or 'api' but share the
        same governed execution path through agent.handle)."""
        providers = _provider_map()
        a, b = providers[id_a], providers[id_b]
        # Both must be either local or api (both use agent.handle internally)
        assert a.spec.kind in ("local", "api"), f"{id_a} kind={a.spec.kind}"
        assert b.spec.kind in ("local", "api"), f"{id_b} kind={b.spec.kind}"


class TestCLIInterchangeability:
    """CLI providers must be structurally interchangeable."""

    @pytest.mark.parametrize("id_a,id_b", _CLI_PAIRS)
    def test_same_contract_shape(self, id_a: str, id_b: str):
        """CLI providers must have the same contract shape."""
        providers = _provider_map()
        a, b = providers[id_a], providers[id_b]
        assert a.spec.kind == "cli"
        assert b.spec.kind == "cli"
        assert a.spec.max_risk_tier == b.spec.max_risk_tier == 4
        assert a.spec.contract_version == b.spec.contract_version == CONTRACT_VERSION

    @pytest.mark.parametrize("id_a,id_b", _CLI_PAIRS)
    def test_both_have_command(self, id_a: str, id_b: str):
        """CLI providers must have a command tuple."""
        providers = _provider_map()
        a, b = providers[id_a], providers[id_b]
        assert isinstance(a.spec.command, tuple)
        assert isinstance(b.spec.command, tuple)
        assert len(a.spec.command) > 0
        assert len(b.spec.command) > 0


class TestPaseoInterchangeability:
    """Paseo providers must be structurally interchangeable."""

    @pytest.mark.parametrize("id_a,id_b", _PASEO_PAIRS)
    def test_same_contract_shape(self, id_a: str, id_b: str):
        """Paseo providers must have the same contract shape."""
        providers = _provider_map()
        a, b = providers[id_a], providers[id_b]
        assert a.spec.kind == "paseo"
        assert b.spec.kind == "paseo"
        assert a.spec.max_risk_tier == b.spec.max_risk_tier == 4
        assert a.spec.contract_version == b.spec.contract_version == CONTRACT_VERSION


# ---------------------------------------------------------------------------
# Behavioral interchangeability
# ---------------------------------------------------------------------------


@dataclass
class _StubHandleResult:
    """The slice of ``agent.handle()``'s HandleResult that every provider
    reads when it builds its ProviderResult."""

    ok: bool = True
    trace: Optional[Dict[str, Any]] = None
    deterministic_hash: str = "hash-1"
    run_id: str = "run-1"
    error: Optional[str] = None


# Which collaborator attributes each provider needs faked so ``execute()``
# stays hermetic. ``_spine`` matters: leaving it None makes the provider build
# a real DecisionEvidenceStore against the live settings paths.
_COLLABORATORS: Dict[str, Tuple[str, ...]] = {
    "local.slice": ("_client", "_provider", "_gate", "_spine"),
    "api.anthropic": ("_client", "_spine"),
}


def _install_stub_handle(monkeypatch, result: _StubHandleResult) -> List[Dict[str, Any]]:
    """Replace the ``agent.handle`` boundary both providers drive.

    Both LocalAgentProvider and AnthropicAgentProvider import ``handle``
    lazily inside ``execute()``, so patching the attribute on the module is
    enough to intercept them — no model, DB, or network is touched.
    """
    import msb_v3.agent.handle as handle_module

    calls: List[Dict[str, Any]] = []

    async def fake_handle(goal: str, **kwargs: Any) -> _StubHandleResult:
        calls.append({"goal": goal, **kwargs})
        return result

    monkeypatch.setattr(handle_module, "handle", fake_handle)
    return calls


def _hermetic_provider(provider_id: str) -> AgentProvider:
    """A registry provider with its collaborators faked, so no concrete
    provider class has to be imported here (the registry stays the only
    source of provider identity, exactly as a consumer sees it)."""
    provider = _provider_map()[provider_id]
    for attr in _COLLABORATORS[provider_id]:
        setattr(provider, attr, object())
    return provider


def _shape(result: ProviderResult) -> Dict[str, str]:
    """Structural fingerprint: field name -> runtime type name."""
    return {f.name: type(getattr(result, f.name)).__name__ for f in fields(ProviderResult)}


def _equal_but_for_duration(a: ProviderResult, b: ProviderResult) -> bool:
    """Value-identical apart from duration_s, which is wall-clock by design."""
    return replace(a, duration_s=0.0) == replace(b, duration_s=0.0)


class TestBehavioralInterchangeability:
    """Both providers must return the same result shape from execute(),
    proving the caller doesn't need provider-specific code.

    Hermetic: the ``agent.handle`` boundary the providers drive is stubbed,
    so what is asserted is each provider's own result construction — not the
    governed loop behind it.
    """

    @pytest.mark.parametrize("provider_id", _INTERCHANGEABLE_IDS)
    def test_execute_returns_provider_result(self, provider_id: str, monkeypatch):
        """execute() must return a fully-populated ProviderResult."""
        _install_stub_handle(monkeypatch, _StubHandleResult())
        result = asyncio.run(_hermetic_provider(provider_id).execute("goal"))
        assert isinstance(result, ProviderResult), (
            f"{provider_id}: execute() returned {type(result).__name__}, not ProviderResult"
        )
        assert result.ok is True
        assert _shape(result) == {
            "ok": "bool",
            "output": "str",
            "artifacts": "dict",
            "error": "NoneType",
            "duration_s": "float",
        }, f"{provider_id}: unexpected result shape {_shape(result)}"

    def test_execute_result_is_identical_across_providers(self, monkeypatch):
        """Same stubbed handle output => value-identical ProviderResult from
        every provider in the pair. This is the actual interchangeability
        claim: given identical inputs the caller cannot tell which provider
        ran, so no provider-specific handling is required."""
        _install_stub_handle(monkeypatch, _StubHandleResult(trace={"outcome": {"x": 1}}))
        local = asyncio.run(_hermetic_provider("local.slice").execute("goal"))
        anthropic = asyncio.run(_hermetic_provider("api.anthropic").execute("goal"))
        assert _shape(local) == _shape(anthropic), (
            f"result shapes diverge: {_shape(local)} != {_shape(anthropic)}"
        )
        assert _equal_but_for_duration(local, anthropic), (
            f"results differ for identical input:\n  local.slice   = {local}\n"
            f"  api.anthropic = {anthropic}"
        )

    def test_execute_failure_shape_is_identical_across_providers(self, monkeypatch):
        """The failure path is interchangeable too — a caller must be able to
        read ``ok``/``error`` without knowing which provider failed."""
        _install_stub_handle(monkeypatch, _StubHandleResult(ok=False, error="boom"))
        local = asyncio.run(_hermetic_provider("local.slice").execute("goal"))
        anthropic = asyncio.run(_hermetic_provider("api.anthropic").execute("goal"))
        assert local.ok is False and anthropic.ok is False
        assert local.error == anthropic.error == "boom"
        assert _shape(local) == _shape(anthropic)
        assert _equal_but_for_duration(local, anthropic)

    def test_both_providers_drive_the_same_governed_surface(self, monkeypatch):
        """Both providers must drive the same governed loop: the goal plus the
        full documented handle() surface (client/spine/session/tenant/approve/
        output_dir), so neither carries a provider-specific execution
        protocol. A provider-specific extra is allowed; an unexpected kwarg —
        or a missing governed one — is not."""
        calls = _install_stub_handle(monkeypatch, _StubHandleResult())
        for provider_id in _INTERCHANGEABLE_IDS:
            asyncio.run(_hermetic_provider(provider_id).execute("goal", session="s1"))
        assert len(calls) == 2

        permitted = _GOVERNED_HANDLE_KWARGS | _ALLOWED_EXTRA_HANDLE_KWARGS
        for call in calls:
            keys = frozenset(call) - {"goal"}
            assert _GOVERNED_HANDLE_KWARGS <= keys, (
                f"a provider skipped governed kwargs: {sorted(_GOVERNED_HANDLE_KWARGS - keys)}"
            )
            assert keys <= permitted, (
                f"a provider passed unvetted handle() kwargs: {sorted(keys - permitted)}"
            )

        common = frozenset.intersection(*(frozenset(c) - {"goal"} for c in calls))
        assert common == _GOVERNED_HANDLE_KWARGS, (
            f"the common handle() surface is {sorted(common)}, "
            f"expected {sorted(_GOVERNED_HANDLE_KWARGS)}"
        )
        assert all(c["goal"] == "goal" and c["session"] == "s1" for c in calls)

    @pytest.mark.parametrize(
        "provider_id", [p.spec.provider_id for p in default_providers()]
    )
    def test_execute_annotation_is_provider_result(self, provider_id: str):
        """Every provider must declare ProviderResult as its return type.

        ``providers.py`` defers annotations, so the signature must be
        evaluated (``eval_str=True``) — the un-evaluated form is the string
        ``'ProviderResult'``, which is why a plain identity check here used to
        be written as an assertion that could never fail.
        """
        provider = _provider_map()[provider_id]
        annotation = inspect.signature(provider.execute, eval_str=True).return_annotation
        assert annotation is ProviderResult, (
            f"{provider_id}: execute() annotated {annotation!r}, not ProviderResult"
        )

    @pytest.mark.parametrize("provider_id", _INTERCHANGEABLE_IDS)
    def test_health_returns_same_shape(self, provider_id: str):
        """health() must return a dict with 'ok' key for all providers."""
        provider = _provider_map()[provider_id]
        result = provider.health()
        assert isinstance(result, dict)
        assert "ok" in result
        assert isinstance(result["ok"], bool)

    @pytest.mark.parametrize("provider_id", ["local.slice", "api.anthropic"])
    def test_available_returns_bool(self, provider_id: str):
        """available() must return a bool for all providers."""
        provider = _provider_map()[provider_id]
        assert isinstance(provider.available(), bool)


# ---------------------------------------------------------------------------
# Registry interchangeability
# ---------------------------------------------------------------------------


class TestRegistryInterchangeability:
    """ProviderRegistry.select() must return providers that are
    interchangeable within the same tier/capability constraints."""

    def test_select_by_capabilities_returns_interchangeable(self):
        """Selecting by capabilities must return providers that share
        the same contract shape — any of them can satisfy the request."""
        from msb_v3.agent.providers import ProviderRegistry
        registry = ProviderRegistry()
        # Select providers that can do search_query + chat
        selected = registry.select(
            required_capabilities=("search_query", "chat"),
            available_only=False,
        )
        assert len(selected) >= 2, (
            f"Expected at least 2 interchangeable providers for "
            f"search_query+chat, got {len(selected)}"
        )
        # All selected providers must have the same capabilities
        cap_sets = [set(p.spec.capabilities) for p in selected]
        assert all(caps == cap_sets[0] for caps in cap_sets), (
            f"Selected providers have different capabilities: {cap_sets}"
        )

    def test_select_by_tier_returns_interchangeable(self):
        """Selecting by max_risk_tier must return providers that are
        all within the same tier — any of them can satisfy the request."""
        from msb_v3.agent.providers import ProviderRegistry
        registry = ProviderRegistry()
        # Select tier-1 providers
        selected = registry.select(max_risk_tier=1, available_only=False)
        for p in selected:
            assert p.spec.max_risk_tier <= 1

    def test_registry_deterministic(self):
        """ProviderRegistry must return the same results on repeated calls."""
        from msb_v3.agent.providers import ProviderRegistry
        registry = ProviderRegistry()
        result1 = registry.list()
        result2 = registry.list()
        assert result1 == result2

    def test_all_conforming_providers_in_registry(self):
        """Every production provider must be in the default registry."""
        from msb_v3.agent.providers import ProviderRegistry
        registry = ProviderRegistry()
        registered_ids = {p["provider_id"] for p in registry.list()}
        expected_ids = {p.spec.provider_id for p in default_providers()}
        assert registered_ids == expected_ids, (
            f"Registry missing: {expected_ids - registered_ids}, "
            f"extra: {registered_ids - expected_ids}"
        )
