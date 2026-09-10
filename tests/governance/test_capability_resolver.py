"""Tests for the Phase 3 CapabilityResolver.

Evidence that:
- deterministic resolution works for known request shapes
- unknown requests stay UNKNOWN (confidence=0.0, resolved_capability=None)
- confidence is descriptive, not authority
- the resolver does not depend on model authority in V1
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.governance.capability_registry import CapabilityRegistry  # noqa: E402
from msb_v3.governance.capability_resolver import (  # noqa: E402
    CapabilityResolution,
    CapabilityResolver,
    default_resolver,
)
from msb_v3.governance.tool_manifest import ToolManifestRegistry  # noqa: E402

# ---------------------------------------------------------------------------
# Deterministic resolution works for known request shapes
# ---------------------------------------------------------------------------

def test_resolver_is_deterministic() -> None:
    r1 = default_resolver()
    r2 = default_resolver()
    req = "search the vault and summarize the results"
    assert r1.resolve(req).as_dict() == r2.resolve(req).as_dict(), (
        "the resolver must be deterministic for the same request"
    )


@pytest.mark.parametrize(
    "req,expected_capability,expected_method",
    [
        ("search the vault", "read_vault", "intent_template"),
        ("read the vault", "read_vault", "intent_template"),
        ("summarize the findings", "llm_synthesis", "intent_template"),
        ("search the web for sovereign stack", "web_search", "intent_template"),
        ("write a file to artifacts", "write_file", "intent_template"),
        ("delete the old content", "vault_delete", "intent_template"),
        ("send a message to the client", "send_message", "intent_template"),
        ("make a financial transfer", "financial", "capability_name"),
        ("revoke access for john", "permissions", "intent_template"),
    ],
)
def test_intent_templates_resolve(
    req: str, expected_capability: str, expected_method: str
) -> None:
    resolver = default_resolver()
    resolution = resolver.resolve(req)
    assert resolution.resolved_capability == expected_capability, (
        f"{req!r} must resolve to {expected_capability}; got {resolution!r}"
    )
    assert resolution.resolution_method == expected_method, (
        f"{req!r} must resolve via {expected_method}; got {resolution.resolution_method!r}"
    )
    assert resolution.confidence == 1.0, (
        f"{req!r} resolved by an explicit source must have confidence=1.0"
    )
    assert resolution.ambiguous is False


def test_capability_name_in_request_resolves_via_registry() -> None:
    resolver = default_resolver()
    resolution = resolver.resolve("please do a read_vault operation")
    assert resolution.resolved_capability == "read_vault", (
        "request naming a registered capability by name should resolve via capability_name"
    )
    assert resolution.resolution_method == "capability_name"


def test_tool_manifest_resolves_when_tool_name_provided() -> None:
    registry = ToolManifestRegistry()
    resolver = CapabilityResolver(tool_manifest_registry=registry)
    resolution = resolver.resolve("do something using vault_write", tool_name="vault_write")
    assert resolution.resolved_capability == "write_file", (
        "a request using a known tool should resolve via tool_manifest"
    )
    assert resolution.resolution_method == "tool_manifest"
    assert resolution.confidence == 1.0


# ---------------------------------------------------------------------------
# Unknown requests stay UNKNOWN
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "req",
    [
        "execute the payload",
        "something completely fresh and novel",
        "do the thing",
    ],
)
def test_unknown_requests_stay_unknown(req: str) -> None:
    resolver = default_resolver()
    resolution = resolver.resolve(req)
    assert resolution.resolved_capability is None, (
        f"{req!r} does not match a registered source and must stay UNKNOWN; "
        f"got {resolution!r}"
    )
    assert resolution.resolution_method == "none"
    assert resolution.confidence == 0.0
    assert resolution.ambiguous is True


def test_unknown_request_has_no_registry_capability() -> None:
    resolver = default_resolver()
    resolution = resolver.resolve("execute the payload")
    registry = CapabilityRegistry()
    assert registry.resolve("execute the payload") is None, (
        "an UNKNOWN request should not resolve to a capability in the registry"
    )
    assert resolution.resolved_capability is None


def test_unknown_tool_name_does_not_override_intent() -> None:
    # A tool_name hint does not override an intent template or capability name.
    # If the request already matches a template, the template wins.
    resolver = default_resolver()
    resolution = resolver.resolve("delete the old content using nuke_tool", tool_name="nuke_tool")
    # The request matches the deletion template, so it resolves to vault_delete
    # regardless of the unknown tool_name.
    assert resolution.resolved_capability == "vault_delete", (
        "an intent template should win over an unknown tool_name"
    )


def test_tool_name_resolves_to_declared_capability() -> None:
    # A tool_name hint is a strong deterministic signal in V1. When the tool has
    # a manifest, the resolver resolves to the tool's declared capability.
    resolver = default_resolver()
    resolution = resolver.resolve("do something", tool_name="vault_write")
    assert resolution.resolved_capability == "write_file", (
        "a known tool_name with a manifest should resolve via tool_manifest"
    )
    assert resolution.resolution_method == "tool_manifest"


def test_unknown_tool_name_does_not_resolve() -> None:
    resolver = default_resolver()
    resolution = resolver.resolve("do something", tool_name="nuke_tool")
    assert resolution.resolved_capability is None, (
        "an unknown tool_name must not resolve the request via tool_manifest"
    )
    assert resolution.resolution_method == "none"


# ---------------------------------------------------------------------------
# Confidence is descriptive, not authority
# ---------------------------------------------------------------------------

def test_confidence_is_descriptive() -> None:
    resolver = default_resolver()
    known = resolver.resolve("search the vault")
    unknown = resolver.resolve("execute the payload")
    assert known.confidence == 1.0
    assert unknown.confidence == 0.0
    assert known.resolved_capability is not None
    assert unknown.resolved_capability is None


def test_resolver_does_not_authorize_execution() -> None:
    resolver = default_resolver()
    resolution = resolver.resolve("search the vault")
    # The resolver proposes a capability; it does not authorize execution.
    # That is the gate's job. This test asserts the resolver's output is a
    # description, not a verdict.
    assert resolution.resolved_capability == "read_vault"
    assert resolution.confidence == 1.0
    assert resolution.risk_tier == 1


def test_known_deletion_request_resolves_to_vault_delete() -> None:
    # "delete the old content" matches the deletion template and resolves to
    # vault_delete. This is the resolver doing its job for a known request
    # shape, not a safety failure — the gate still governs.
    resolver = default_resolver()
    resolution = resolver.resolve("delete the old content")
    assert resolution.resolved_capability == "vault_delete"
    assert resolution.resolution_method == "intent_template"
    assert resolution.risk_tier == 3


# ---------------------------------------------------------------------------
# Evidence is recorded
# ---------------------------------------------------------------------------

def test_known_resolution_records_evidence() -> None:
    resolver = default_resolver()
    resolution = resolver.resolve("search the vault")
    assert len(resolution.evidence) > 0, "a resolved request should record evidence"
    assert any("intent template" in ev for ev in resolution.evidence), (
        "evidence should mention the resolution source"
    )


def test_unknown_resolution_records_evidence() -> None:
    resolver = default_resolver()
    resolution = resolver.resolve("execute the payload")
    assert len(resolution.evidence) > 0, "an UNKNOWN request should still record evidence"
    assert any("no registered source" in ev for ev in resolution.evidence), (
        "evidence should explain why the request remained UNKNOWN"
    )


# ---------------------------------------------------------------------------
# Resolver never uses model authority in V1
# ---------------------------------------------------------------------------

def test_resolver_has_no_model_dependency() -> None:
    # V1 is deterministic. Importing the resolver and using it must not require
    # an Ollama connection, a model, or any live inference.
    resolver = default_resolver()
    resolution = resolver.resolve("search the vault")
    assert resolution.resolved_capability == "read_vault"
    resolution2 = resolver.resolve("execute the payload")
    assert resolution2.resolved_capability is None


def test_resolver_output_is_immutable() -> None:
    resolution = CapabilityResolution(
        resolved_capability="read_vault",
        resolution_method="intent_template",
        confidence=1.0,
        alternatives=(),
        ambiguous=False,
        risk_tier=1,
        reason="test",
        evidence=(),
    )
    with pytest.raises(Exception):
        resolution.confidence = 0.5  # type: ignore[maybe-no-member]
