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

import ast
import asyncio
import inspect
from dataclasses import dataclass, fields, replace
from pathlib import Path
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

# CLI providers that share the same contract shape: the same declared
# capabilities, tier 4, and the same command contract.
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

    def test_declare_no_capabilities_as_a_trust_boundary(self):
        """The CLI workers' capability tuple must stay empty.

        A declared capability is a TRUST grant, not a description of what the
        worker can do: the isolation model is "no implicit trust", and an
        external agent on the operator's account only gets capabilities an
        operator registers with a scope. The temptation is real — an empty tuple
        makes the registry unable to select a CLI worker on capability — but a
        name added here to make routing convenient would be a privilege
        escalation dressed as metadata. Routing uses `kind` instead; see
        tests/integrations/test_cli_provider_isolation.py (and
        TestCliProviderCapabilityEscape for the injection cases).
        """
        for provider_id, provider in _provider_map().items():
            if provider.spec.kind == "cli":
                assert provider.spec.capabilities == (), (
                    f"{provider_id} declares {provider.spec.capabilities} — a CLI "
                    f"worker holds no capabilities until an operator grants them"
                )

    def test_kind_is_the_routing_key_for_cli(self):
        """Why the empty tuple above is not a blocker: `kind` is the documented
        routing key for CLI workers, and the factory uses it (see
        `builders._WORKER_KIND`)."""
        from msb_v3.factory.builders import _WORKER_KIND

        cli_ids = [pid for pid, p in _provider_map().items() if p.spec.kind == "cli"]
        assert cli_ids, "no CLI workers in the default registry"
        assert _WORKER_KIND == "cli", (
            f"the factory routes on kind={_WORKER_KIND!r} but its workers are cli"
        )


class TestProviderCapabilityVocabulary:
    """A declared capability must be a name the governance layer knows.

    Two tables carry the vocabulary: `safety.TOOL_CAPABILITY` (governed tool
    names) and each ToolDef's `required_capabilities` in `tools/registry.py`.
    Declaring anything else would be a claim nothing can select on or gate —
    `governance.capability_registry` resolves unknown ids to None by design, so
    an invented name would look registered while resolving to nothing.

    This constrains the providers that DO declare capabilities (local.slice,
    api.anthropic); the CLI workers declare none at all, by design — see the
    trust-boundary test in TestCLIInterchangeability.
    """

    def test_every_declared_capability_comes_from_a_known_table(self):
        from msb_v3.agent.safety import TOOL_CAPABILITY
        from msb_v3.tools.registry import TOOLS

        known = set(TOOL_CAPABILITY) | {
            cap for tool in TOOLS.values() for cap in tool.required_capabilities
        }
        offenders = {
            p.spec.provider_id: sorted(set(p.spec.capabilities) - known)
            for p in default_providers()
            if set(p.spec.capabilities) - known
        }
        assert not offenders, (
            f"provider(s) declare capabilities in no known table: {offenders} — "
            f"add the name to TOOL_CAPABILITY or to a ToolDef, or do not declare it"
        )


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


# ---------------------------------------------------------------------------
# Consumer role: the factory must SELECT a worker, never build one
# ---------------------------------------------------------------------------

_BUILDERS_SOURCE = (
    Path(__file__).resolve().parents[2] / "src" / "msb_v3" / "factory" / "builders.py"
)


def _providers_imported_by(source: Path) -> set[str]:
    """Names imported by a module from ``msb_v3.agent.providers``."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "msb_v3.agent.providers"
        for alias in node.names
    }


class _StubSpec:
    provider_id = "cli.stub"
    display_name = "stub CLI worker"
    kind = "cli"
    capabilities: Tuple[str, ...] = ()
    max_risk_tier = 4
    timeout_s = 300.0
    command = ("stub",)


class _StubWorker:
    """A CLI-kind worker carrying the Spec fields the registry selects on."""

    def __init__(self, *, available: bool = True) -> None:
        self.spec = _StubSpec()
        self._available = available

    def available(self) -> bool:
        return self._available

    def unavailable_reason(self) -> str:
        return "" if self._available else "stub not on PATH"


class _StubRegistry:
    """Stands in for ProviderRegistry, recording the selection inputs it got."""

    def __init__(self, worker: _StubWorker) -> None:
        self._worker = worker
        self.calls: List[Dict[str, Any]] = []

    def select(
        self,
        *,
        required_capabilities: Tuple[str, ...] = (),
        max_risk_tier: int = 4,
        available_only: bool = True,
    ) -> List[Any]:
        self.calls.append(
            {
                "available_only": available_only,
                "max_risk_tier": max_risk_tier,
                "required_capabilities": required_capabilities,
            }
        )
        if available_only and not self._worker.available():
            return []
        return [self._worker]


class TestFactoryBuilderIsARegistryConsumer:
    """`factory/builders.py` is a Consumer: it must select its worker THROUGH the
    registry, never construct a Provider.

    Its default used to be a direct ``CliAgentProvider(("claude", "-p"))`` in the
    consumer file, which pinned the factory to one binary on PATH, ignored the
    risk tier, and made `TestRegistryInterchangeability` below a claim about a
    seam the factory did not actually use: the registry could flip providers
    while the consumer kept its own.
    """

    def test_consumer_names_no_concrete_provider(self):
        """The Definition is allowed; any other ``*Provider`` import or call in a
        consumer file is the seam leak. Parsed, not grepped, so the file may
        still *discuss* the old class in a comment without failing."""
        tree = ast.parse(_BUILDERS_SOURCE.read_text(encoding="utf-8"))
        offenders = sorted(
            {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "msb_v3.agent.providers"
                for alias in node.names
                if alias.name.endswith("Provider") and alias.name != "AgentProvider"
            }
            | {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id.endswith("Provider")
                and node.func.id != "AgentProvider"
            }
        )
        assert not offenders, (
            f"factory/builders.py names concrete provider(s) {offenders} — a Consumer "
            f"selects through ProviderRegistry; naming a Provider is the seam leak"
        )

    def test_consumer_selects_through_the_registry(self):
        """The positive half: closing the leak by dropping the provider call
        without importing the Registry would just be a different hardcode."""
        assert "ProviderRegistry" in _providers_imported_by(_BUILDERS_SOURCE)

    def test_the_registry_decides_which_worker_the_factory_gets(self):
        """Swap the registered worker and the factory's worker changes with no
        consumer edit — the property the seam exists to provide."""
        from msb_v3.factory.builders import CliAgentBuilder

        worker = _StubWorker()
        registry = _StubRegistry(worker)
        builder = CliAgentBuilder(registry=registry)
        assert builder._provider is worker
        # The worker's identity drives the reviewer-panel invariant
        # ("cli.stub" -> "stub"), so it must come from the selected spec.
        assert builder.model == "stub"
        assert registry.calls[0]["available_only"] is True
        assert registry.calls[0]["max_risk_tier"] == 4

    def test_unavailable_worker_is_kept_so_build_can_report_why(self):
        """With nothing available the builder must still hold a worker and say
        why, because `build()` records `unavailable_reason()` as a BuildResult
        error — raising here would turn four recorded failures into 500s."""
        from msb_v3.factory.builders import CliAgentBuilder

        worker = _StubWorker(available=False)
        registry = _StubRegistry(worker)
        builder = CliAgentBuilder(registry=registry)
        assert builder._provider is worker
        assert builder._provider.unavailable_reason()
        assert [c["available_only"] for c in registry.calls] == [True, False], (
            "expected an available-only selection followed by an availability-ignored one"
        )


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
        """``max_risk_tier`` is an UPPER bound: every selected provider stays
        within it, and widening it only adds.

        The bound is asserted at tiers that exist. The previous version of this
        test selected ``max_risk_tier=1``, which no provider satisfies (the
        lowest in the registry is 3), so its loop iterated an empty list and
        passed without asserting anything — a test named for a property it
        never checked. A non-empty floor is therefore part of the claim, not
        decoration: an empty selection must not read as success.
        """
        from msb_v3.agent.providers import ProviderRegistry
        registry = ProviderRegistry()

        widest = registry.select(max_risk_tier=4, available_only=False)
        assert widest, "no providers at the widest tier — registry is empty"

        bounded = registry.select(max_risk_tier=3, available_only=False)
        assert bounded, (
            "no providers within tier 3 — the bound excludes everything, so "
            "this test could not observe a broken bound"
        )
        for provider in bounded:
            assert provider.spec.max_risk_tier <= 3, (
                f"{provider.spec.provider_id} tier={provider.spec.max_risk_tier} "
                f"exceeds the requested bound of 3"
            )
        for provider in widest:
            assert provider.spec.max_risk_tier <= 4

        # The interchangeability pair must be selectable within its own tier...
        bounded_ids = {p.spec.provider_id for p in bounded}
        missing = set(_INTERCHANGEABLE_IDS) - bounded_ids
        assert not missing, (
            f"interchangeable providers not selectable at tier 3: {sorted(missing)}"
        )
        # ...and the bound must actually exclude, or widening it would be a no-op
        # and this test could not detect a tier filter that stopped filtering.
        assert bounded_ids <= {p.spec.provider_id for p in widest}, (
            "widening the tier bound dropped providers"
        )
        assert len(bounded) < len(widest), (
            f"tier-3 selection ({len(bounded)}) equals the full registry "
            f"({len(widest)}) — the tier bound is not filtering anything"
        )

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
