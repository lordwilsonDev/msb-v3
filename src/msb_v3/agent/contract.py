"""ProviderContract v1 — the versioned contract every provider must satisfy.

This module defines the contract boundary: what a provider must declare,
what it must do, and what the runtime can depend on. The conformance suite
(tests/contracts/test_provider_contract.py) verifies every production
provider against this contract.

Design principle (convergence blueprint §13–§18):
    Do not create a decorative interface.
    The contract must represent the actual provider boundary.

The contract is built on top of the existing ``ProviderSpec`` and
``AgentProvider`` ABC. It adds:
    - A version field (``contract_version``) for forward compatibility
    - A ``health()`` method for runtime health checks
    - Explicit error semantics (fail-closed by default)
    - Governance metadata (what governance layers apply)
    - Evidence metadata (what evidence does the provider produce)

ProviderContract v1 conformance:
    Every registered provider must satisfy all invariants below.
    The conformance suite tests these mechanically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple

# Contract version — bump when the contract interface changes.
CONTRACT_VERSION = "1"

# Valid provider kinds (from ProviderSpec.kind).
VALID_KINDS = frozenset({"local", "cli", "dsh", "api", "paseo"})


@dataclass(frozen=True)
class ProviderContract:
    """Versioned provider contract v1.

    This is the **specification**, not the implementation. A provider
    conforms to this contract by satisfying all invariants. The conformance
    suite tests these mechanically.

    Invariants:
        1. provider_id is non-empty and unique across all registered providers.
        2. display_name is non-empty.
        3. kind is one of VALID_KINDS.
        4. capabilities is a tuple of strings (may be empty for CLI/DSH workers).
        5. max_risk_tier is in [1, 4].
        6. timeout_s is > 0.
        7. contract_version == CONTRACT_VERSION ("1").
        8. fail_closed is True (providers must fail closed on error).
        9. produces_evidence is True (every execution must leave an audit trail).
    """

    # Identity
    provider_id: str
    display_name: str
    kind: str  # "local" | "cli" | "dsh" | "api" | "paseo"

    # Capabilities
    capabilities: Tuple[str, ...] = ()
    max_risk_tier: int = 2

    # Timeouts
    timeout_s: float = 120.0

    # Contract version — must match CONTRACT_VERSION for v1 conformance.
    contract_version: str = CONTRACT_VERSION

    # Error semantics
    fail_closed: bool = True  # provider fails closed on error (always True for v1)

    # Governance
    requires_approval: bool = False  # operator approval required for execution

    # Evidence
    produces_evidence: bool = True  # provider produces evidence receipt

    # Health
    supports_health_check: bool = True  # provider implements health()


def contract_from_spec(spec: Any) -> ProviderContract:
    """Derive a ProviderContract from a ProviderSpec instance.

    This is the bridge between the existing ProviderSpec and the new
    ProviderContract. Providers that already have a ProviderSpec can be
    automatically wrapped.
    """
    return ProviderContract(
        provider_id=spec.provider_id,
        display_name=spec.display_name,
        kind=spec.kind,
        capabilities=spec.capabilities,
        max_risk_tier=spec.max_risk_tier,
        timeout_s=spec.timeout_s,
        contract_version=spec.contract_version if hasattr(spec, "contract_version") else CONTRACT_VERSION,
    )


def validate_contract(contract: ProviderContract) -> list[str]:
    """Validate a ProviderContract. Returns a list of error messages
    (empty = valid).

    This is used by the conformance suite to verify contract invariants.
    """
    errors: list[str] = []

    if not contract.provider_id:
        errors.append("provider_id is empty")

    if not contract.display_name:
        errors.append("display_name is empty")

    if contract.kind not in VALID_KINDS:
        errors.append(f"kind must be one of {sorted(VALID_KINDS)}, got {contract.kind!r}")

    if contract.max_risk_tier < 1 or contract.max_risk_tier > 4:
        errors.append(f"max_risk_tier must be in [1, 4], got {contract.max_risk_tier}")

    if contract.timeout_s <= 0:
        errors.append(f"timeout_s must be > 0, got {contract.timeout_s}")

    if contract.contract_version != CONTRACT_VERSION:
        errors.append(f"contract_version must be {CONTRACT_VERSION!r}, got {contract.contract_version!r}")

    if not contract.fail_closed:
        errors.append("fail_closed must be True (v1 invariant)")

    if not contract.produces_evidence:
        errors.append("produces_evidence must be True (v1 invariant)")

    return errors
