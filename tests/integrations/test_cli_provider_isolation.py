"""C5: CLI Provider Isolation Tests — proves capability escape is closed.

The CliAgentProvider runs external agents (Claude Code, Codex, OpenCode) as
subprocesses. The safety model is:
  1. CLI providers declare NO capabilities (empty tuple)
  2. They run in isolated worktrees (temp directories)
  3. They respect timeout bounds
  4. They respect output bounds
  5. Operator registration with scoped capabilities is required for real work

This test suite verifies that the CLI provider cannot escape its isolation
boundary — i.e., it cannot access capabilities it wasn't granted.
"""
from __future__ import annotations

import os
from typing import Tuple

import pytest

from msb_v3.agent.providers import (
    CliAgentProvider,
    ProviderRegistry,
    ProviderSpec,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cli_provider(
    command: Tuple[str, ...] = ("echo",),
    provider_id: str = "cli.test",
    timeout_s: float = 5.0,
) -> CliAgentProvider:
    """Create a CLI provider with minimal config."""
    return CliAgentProvider(
        command=command,
        provider_id=provider_id,
        timeout_s=timeout_s,
    )


# ---------------------------------------------------------------------------
# Test 1: CLI provider declares NO capabilities
# ---------------------------------------------------------------------------

class TestCliProviderCapabilities:
    """The CLI provider must have zero capabilities by default."""

    def test_cli_provider_has_no_capabilities(self):
        """Capability tuple must be empty — no implicit trust."""
        provider = _make_cli_provider()
        assert provider.spec.capabilities == (), (
            "CLI provider must declare zero capabilities; "
            "got: {}".format(provider.spec.capabilities)
        )

    def test_cli_provider_max_risk_tier_is_4(self):
        """CLI providers are HIGH risk (tier 4) by construction."""
        provider = _make_cli_provider()
        assert provider.spec.max_risk_tier == 4

    def test_cli_provider_kind_is_cli(self):
        """Provider kind must be 'cli' for registry routing."""
        provider = _make_cli_provider()
        assert provider.spec.kind == "cli"


# ---------------------------------------------------------------------------
# Test 2: CLI provider runs in isolated worktree
# ---------------------------------------------------------------------------

class TestCliProviderWorktreeIsolation:
    """The CLI provider must execute in a temp directory, not the host cwd."""

    @pytest.mark.asyncio
    async def test_execute_creates_temp_worktree(self):
        """Execute must create a temp directory and run the command there."""
        provider = _make_cli_provider(command=("sh", "-c", "pwd"))

        result = await provider.execute("test goal")

        assert result.ok, f"Provider failed: {result.error}"
        # The output should be a temp path, not the project root
        output = result.output.strip()
        assert output != os.getcwd(), (
            "CLI provider ran in host cwd instead of isolated worktree"
        )
        assert "msb_" in output or "cli" in output.lower(), (
            f"Expected temp worktree path, got: {output}"
        )

    @pytest.mark.asyncio
    async def test_execute_sets_msb_worktree_env(self):
        """MSB_WORKTREE must point at a real isolated directory the subprocess
        runs in. Checked from inside the subprocess (`test -d`) — the provider
        reaps the worktree once execute() returns, so a post-hoc exists() check
        would race the cleanup."""
        provider = _make_cli_provider(
            command=("sh", "-c", 'test -d "$MSB_WORKTREE" && printf "%s" "$MSB_WORKTREE"')
        )

        result = await provider.execute("env check")

        assert result.ok, f"Provider failed: {result.error}"
        output = result.output.strip()
        assert output and output != "", "MSB_WORKTREE env var not set / not a directory"
        assert output != os.getcwd(), "worktree must not be the host cwd"
        assert "cli" in output.lower() or "msb_" in output, (
            f"expected an isolated temp worktree path, got: {output}"
        )


# ---------------------------------------------------------------------------
# Test 3: CLI provider respects timeout bounds
# ---------------------------------------------------------------------------

class TestCliProviderTimeout:
    """The CLI provider must kill long-running subprocesses."""

    @pytest.mark.asyncio
    async def test_timeout_kills_slow_process(self):
        """A process exceeding timeout_s must be killed."""
        # Use a shell command that sleeps for a long time
        provider = _make_cli_provider(
            command=("sh", "-c", "sleep 30"),
            timeout_s=0.5,
        )

        result = await provider.execute("slow goal")

        assert not result.ok
        assert result.error is not None
        # Could be timeout or process killed — both indicate the bound worked

    @pytest.mark.asyncio
    async def test_fast_process_completes_within_timeout(self):
        """A fast process must complete normally."""
        provider = _make_cli_provider(
            command=("echo", "fast"),
            timeout_s=5.0,
        )

        result = await provider.execute("fast goal")

        assert result.ok
        assert "fast" in (result.output or "")


# ---------------------------------------------------------------------------
# Test 4: CLI provider error handling
# ---------------------------------------------------------------------------

class TestCliProviderErrorHandling:
    """The CLI provider must handle errors gracefully without leaking state."""

    @pytest.mark.asyncio
    async def test_nonexistent_command_returns_error(self):
        """A missing binary must return an error, not crash."""
        provider = _make_cli_provider(command=("nonexistent_binary_xyz",))

        result = await provider.execute("missing binary goal")

        assert not result.ok
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_unavailable_provider_returns_error(self):
        """An unavailable provider must return a clear error message."""
        provider = _make_cli_provider(command=("nonexistent_xyz",))

        assert not provider.available()
        reason = provider.unavailable_reason()
        assert "not on PATH" in reason

    @pytest.mark.asyncio
    async def test_failing_command_returns_exit_code(self):
        """A command that exits non-zero must report the exit code."""
        provider = _make_cli_provider(command=("python3", "-c", "exit(42)"))

        result = await provider.execute("failing goal")

        assert not result.ok
        assert "42" in (result.error or "")


# ---------------------------------------------------------------------------
# Test 5: CLI provider cannot access host capabilities
# ---------------------------------------------------------------------------

class TestCliProviderCapabilityEscape:
    """Prove that a CLI provider cannot escalate capabilities."""

    def test_registry_does_not_grant_capabilities_to_cli(self):
        """ProviderRegistry must not grant extra capabilities to CLI providers."""
        cli = _make_cli_provider(provider_id="cli.escape_test")
        registry = ProviderRegistry(providers=(cli,))

        # Get the provider back
        registered = registry.get("cli.escape_test")
        assert registered is not None
        assert registered.spec.capabilities == (), (
            "Registry must not add capabilities to CLI providers"
        )

    def test_cli_provider_cannot_claim_sovereign_capabilities(self):
        """A CLI provider must not be able to request sovereign capabilities."""
        provider = _make_cli_provider()

        # Sovereign capabilities that a CLI provider should NEVER have
        sovereign_caps = {
            "governance_override",
            "killswitch_arm",
            "budget_override",
            "provider_registration",
            "capability_grant",
            "evidence_tamper",
            "audit_bypass",
        }

        provider_caps = set(provider.spec.capabilities)
        overlap = sovereign_caps & provider_caps

        assert not overlap, (
            f"CLI provider has sovereign capabilities it shouldn't: {overlap}"
        )

    def test_provider_spec_is_frozen(self):
        """ProviderSpec must be frozen to prevent runtime mutation."""
        spec = ProviderSpec(
            provider_id="test",
            display_name="Test",
            kind="cli",
            command=("echo",),
            capabilities=(),
            max_risk_tier=4,
            timeout_s=5.0,
        )

        with pytest.raises(AttributeError):
            spec.capabilities = ("injected",)  # type: ignore[misc]

    def test_spec_no_default_capabilities(self):
        """Default capabilities must be empty, not a mutable default."""
        spec = ProviderSpec(
            provider_id="test",
            display_name="Test",
            kind="cli",
            command=("echo",),
        )
        assert spec.capabilities == ()

    @pytest.mark.asyncio
    async def test_cli_execute_cannot_inject_capabilities(self):
        """The execute method must not allow capability injection via context."""
        provider = _make_cli_provider(command=("echo", "no_inject"))

        # Attempt to inject capabilities via context
        malicious_context = {
            "capabilities": ["governance_override", "killswitch_arm"],
            "injected": True,
        }

        await provider.execute("injection attempt", context=malicious_context)

        # The provider should still have no capabilities
        assert provider.spec.capabilities == (), (
            "Context injection must not grant capabilities"
        )


# ---------------------------------------------------------------------------
# Test 6: Provider selection respects capability boundaries
# ---------------------------------------------------------------------------

class TestProviderSelectionCapabilities:
    """ProviderRegistry.select() must respect capability requirements."""

    def test_select_without_capabilities_returns_all(self):
        """No capability filter must return all available providers."""
        cli = _make_cli_provider(provider_id="cli.a")
        registry = ProviderRegistry(providers=(cli,))

        selected = registry.select(required_capabilities=())
        assert len(selected) >= 1

    def test_select_with_capabilities_excludes_cli(self):
        """CLI providers (no capabilities) must be excluded when capabilities required."""
        cli = _make_cli_provider(provider_id="cli.b")
        registry = ProviderRegistry(providers=(cli,))

        selected = registry.select(required_capabilities=("search_query",))
        # CLI providers have no capabilities, so they should be excluded
        for p in selected:
            assert "search_query" in p.spec.capabilities

    def test_select_respects_risk_tier(self):
        """Providers above max_risk_tier must be excluded."""
        cli = _make_cli_provider(provider_id="cli.c")
        registry = ProviderRegistry(providers=(cli,))

        # CLI is tier 4; selecting with max_tier=3 should exclude it
        selected = registry.select(max_risk_tier=3)
        for p in selected:
            assert p.spec.max_risk_tier <= 3
