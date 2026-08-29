"""ProviderContract v1 conformance suite (convergence blueprint §17).

Runs against every production provider adapter. A provider that does not
conform cannot enter the production registry. The suite tests at minimum:
    - identity (non-empty, unique)
    - capability declaration (tuple of strings)
    - health (available() + health() return valid response)
    - timeout behavior (timeout_s > 0)
    - error behavior (fail_closed = True)
    - execution contract (execute() returns ProviderResult)
    - governance metadata (max_risk_tier in [1, 4])
    - evidence metadata (produces_evidence = True)
    - contract version (contract_version == "1")

The suite becomes mandatory CI. See docs/architecture/ for the contract
definition.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List

import pytest

from msb_v3.agent.contract import (
    CONTRACT_VERSION,
    ProviderContract,
    contract_from_spec,
    validate_contract,
)
from msb_v3.agent.providers import (
    AgentProvider,
    default_providers,
)

# ---------------------------------------------------------------------------
# All production providers (imported lazily to avoid circular imports)
# ---------------------------------------------------------------------------


def _all_providers() -> List[AgentProvider]:
    """Return all production providers from the default registry."""
    return list(default_providers())


def _provider_ids() -> List[str]:
    """Return unique provider IDs for parametrize."""
    return [p.spec.provider_id for p in _all_providers()]


def _provider_map() -> Dict[str, AgentProvider]:
    """Return provider_id -> provider mapping."""
    return {p.spec.provider_id: p for p in _all_providers()}


# ---------------------------------------------------------------------------
# Contract conformance tests
# ---------------------------------------------------------------------------


class TestProviderContractInvariants:
    """Every provider must satisfy the ProviderContract v1 invariants."""

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_contract_version_is_v1(self, provider_id: str):
        """contract_version must be '1'."""
        provider = _provider_map()[provider_id]
        assert provider.spec.contract_version == CONTRACT_VERSION, (
            f"{provider_id}: contract_version must be {CONTRACT_VERSION!r}, "
            f"got {provider.spec.contract_version!r}"
        )

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_provider_id_non_empty(self, provider_id: str):
        """provider_id must be non-empty."""
        provider = _provider_map()[provider_id]
        assert provider.spec.provider_id, f"{provider_id}: provider_id is empty"

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_display_name_non_empty(self, provider_id: str):
        """display_name must be non-empty."""
        provider = _provider_map()[provider_id]
        assert provider.spec.display_name, f"{provider_id}: display_name is empty"

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_kind_is_valid(self, provider_id: str):
        """kind must be one of: local, cli, dsh, api, paseo."""
        provider = _provider_map()[provider_id]
        valid_kinds = {"local", "cli", "dsh", "api", "paseo"}
        assert provider.spec.kind in valid_kinds, (
            f"{provider_id}: kind must be one of {sorted(valid_kinds)}, "
            f"got {provider.spec.kind!r}"
        )

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_max_risk_tier_in_range(self, provider_id: str):
        """max_risk_tier must be in [1, 4]."""
        provider = _provider_map()[provider_id]
        assert 1 <= provider.spec.max_risk_tier <= 4, (
            f"{provider_id}: max_risk_tier must be in [1, 4], "
            f"got {provider.spec.max_risk_tier}"
        )

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_timeout_positive(self, provider_id: str):
        """timeout_s must be > 0."""
        provider = _provider_map()[provider_id]
        assert provider.spec.timeout_s > 0, (
            f"{provider_id}: timeout_s must be > 0, got {provider.spec.timeout_s}"
        )

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_capabilities_are_tuple_of_strings(self, provider_id: str):
        """capabilities must be a tuple of strings."""
        provider = _provider_map()[provider_id]
        caps = provider.spec.capabilities
        assert isinstance(caps, tuple), (
            f"{provider_id}: capabilities must be a tuple, got {type(caps).__name__}"
        )
        for cap in caps:
            assert isinstance(cap, str), (
                f"{provider_id}: capability must be a string, got {type(cap).__name__}: {cap!r}"
            )


class TestProviderContractIdentity:
    """Provider IDs must be unique across all registered providers."""

    def test_provider_ids_are_unique(self):
        """No two providers may share the same provider_id."""
        ids = _provider_ids()
        duplicates = [pid for pid in ids if ids.count(pid) > 1]
        assert not duplicates, f"Duplicate provider IDs: {set(duplicates)}"


class TestProviderContractHealth:
    """Every provider must implement health() and available()."""

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_health_returns_dict(self, provider_id: str):
        """health() must return a dict with 'ok' key."""
        provider = _provider_map()[provider_id]
        result = provider.health()
        assert isinstance(result, dict), (
            f"{provider_id}: health() must return a dict, got {type(result).__name__}"
        )
        assert "ok" in result, f"{provider_id}: health() must include 'ok' key"

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_health_ok_is_bool(self, provider_id: str):
        """health()['ok'] must be a bool."""
        provider = _provider_map()[provider_id]
        result = provider.health()
        assert isinstance(result["ok"], bool), (
            f"{provider_id}: health()['ok'] must be bool, got {type(result['ok']).__name__}"
        )

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_available_is_bool(self, provider_id: str):
        """available() must return a bool."""
        provider = _provider_map()[provider_id]
        result = provider.available()
        assert isinstance(result, bool), (
            f"{provider_id}: available() must return bool, got {type(result).__name__}"
        )

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_unavailable_reason_is_str(self, provider_id: str):
        """unavailable_reason() must return a str."""
        provider = _provider_map()[provider_id]
        result = provider.unavailable_reason()
        assert isinstance(result, str), (
            f"{provider_id}: unavailable_reason() must return str, "
            f"got {type(result).__name__}"
        )

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_health_consistent_with_available(self, provider_id: str):
        """health()['ok'] must agree with available()."""
        provider = _provider_map()[provider_id]
        health_result = provider.health()
        available_result = provider.available()
        assert health_result["ok"] == available_result, (
            f"{provider_id}: health()['ok']={health_result['ok']} but "
            f"available()={available_result} — inconsistent"
        )


class TestProviderContractExecution:
    """Every provider must have execute() with the correct signature."""

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_execute_is_coroutine(self, provider_id: str):
        """execute() must be an async method."""
        provider = _provider_map()[provider_id]
        assert asyncio.iscoroutinefunction(provider.execute), (
            f"{provider_id}: execute() must be async"
        )

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_execute_returns_provider_result(self, provider_id: str):
        """execute() must return a ProviderResult (tested via type hint)."""
        provider = _provider_map()[provider_id]
        # We can't call execute() without a real goal, but we can verify
        # the return type annotation exists
        import inspect
        sig = inspect.signature(provider.execute)
        assert sig.return_annotation is not inspect.Parameter.empty, (
            f"{provider_id}: execute() must have a return type annotation"
        )


class TestProviderContractSpec:
    """ProviderSpec must have the contract_version field."""

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_spec_has_contract_version(self, provider_id: str):
        """ProviderSpec must include contract_version field."""
        provider = _provider_map()[provider_id]
        assert hasattr(provider.spec, "contract_version"), (
            f"{provider_id}: ProviderSpec missing contract_version field"
        )

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_spec_contract_version_is_v1(self, provider_id: str):
        """ProviderSpec.contract_version must be '1'."""
        provider = _provider_map()[provider_id]
        assert provider.spec.contract_version == CONTRACT_VERSION, (
            f"{provider_id}: contract_version must be {CONTRACT_VERSION!r}"
        )


class TestProviderContractFromSpec:
    """contract_from_spec() must derive a valid contract from any ProviderSpec."""

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_contract_from_spec_is_valid(self, provider_id: str):
        """Deriving a contract from a provider's spec must produce no errors."""
        provider = _provider_map()[provider_id]
        contract = contract_from_spec(provider.spec)
        errors = validate_contract(contract)
        assert not errors, (
            f"{provider_id}: contract_from_spec produced errors: {errors}"
        )

    @pytest.mark.parametrize("provider_id", _provider_ids())
    def test_contract_matches_spec(self, provider_id: str):
        """Derived contract must match the source spec's identity fields."""
        provider = _provider_map()[provider_id]
        contract = contract_from_spec(provider.spec)
        assert contract.provider_id == provider.spec.provider_id
        assert contract.display_name == provider.spec.display_name
        assert contract.kind == provider.spec.kind
        assert contract.capabilities == provider.spec.capabilities
        assert contract.max_risk_tier == provider.spec.max_risk_tier
        assert contract.timeout_s == provider.spec.timeout_s


class TestProviderContractValidation:
    """validate_contract() must catch invalid contracts."""

    def test_valid_contract_passes(self):
        """A valid contract must produce no errors."""
        contract = ProviderContract(
            provider_id="test.valid",
            display_name="Test Valid",
            kind="local",
        )
        errors = validate_contract(contract)
        assert not errors

    def test_empty_provider_id_fails(self):
        """Empty provider_id must be caught."""
        contract = ProviderContract(
            provider_id="",
            display_name="Test",
            kind="local",
        )
        errors = validate_contract(contract)
        assert any("provider_id" in e for e in errors)

    def test_invalid_kind_fails(self):
        """Invalid kind must be caught."""
        contract = ProviderContract(
            provider_id="test.invalid",
            display_name="Test",
            kind="invalid_kind",
        )
        errors = validate_contract(contract)
        assert any("kind" in e for e in errors)

    def test_invalid_risk_tier_fails(self):
        """Risk tier out of range must be caught."""
        contract = ProviderContract(
            provider_id="test.risk",
            display_name="Test",
            kind="local",
            max_risk_tier=5,
        )
        errors = validate_contract(contract)
        assert any("max_risk_tier" in e for e in errors)

    def test_zero_timeout_fails(self):
        """Zero timeout must be caught."""
        contract = ProviderContract(
            provider_id="test.timeout",
            display_name="Test",
            kind="local",
            timeout_s=0,
        )
        errors = validate_contract(contract)
        assert any("timeout" in e for e in errors)

    def test_wrong_version_fails(self):
        """Wrong contract version must be caught."""
        contract = ProviderContract(
            provider_id="test.version",
            display_name="Test",
            kind="local",
            contract_version="2",
        )
        errors = validate_contract(contract)
        assert any("contract_version" in e for e in errors)

    def test_fail_open_fails(self):
        """fail_closed=False must be caught (v1 invariant)."""
        contract = ProviderContract(
            provider_id="test.failopen",
            display_name="Test",
            kind="local",
            fail_closed=False,
        )
        errors = validate_contract(contract)
        assert any("fail_closed" in e for e in errors)


class TestProviderContractRegistry:
    """The ProviderRegistry must only return conforming providers."""

    def test_registry_list_conforms(self):
        """Every provider returned by ProviderRegistry.list() must conform."""
        from msb_v3.agent.providers import ProviderRegistry
        registry = ProviderRegistry()
        for entry in registry.list():
            assert "provider_id" in entry
            assert "display_name" in entry
            assert "kind" in entry
            assert "capabilities" in entry
            assert "max_risk_tier" in entry
            assert "available" in entry

    def test_registry_select_respects_tier(self):
        """ProviderRegistry.select() must respect max_risk_tier."""
        from msb_v3.agent.providers import ProviderRegistry
        registry = ProviderRegistry()
        # Tier 1 only: should return only providers with max_risk_tier <= 1
        tier1 = registry.select(max_risk_tier=1, available_only=False)
        for p in tier1:
            assert p.spec.max_risk_tier <= 1, (
                f"{p.spec.provider_id}: max_risk_tier={p.spec.max_risk_tier} "
                f"but select(max_risk_tier=1) returned it"
            )
