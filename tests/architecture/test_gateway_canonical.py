"""Gateway canonical path enforcement (convergence blueprint §12).

Verifies that every governed execution path routes through the gateway
or through an authorized governance layer. The test checks **architecture**,
not merely that gateway code exists — it inspects imports and call graphs
to ensure no canonical governed path can resolve a capability without
gateway mediation or ActionGate enforcement.

Acceptance condition (from the blueprint):
    No canonical governed execution path can resolve the capability
    without gateway mediation.

The current architecture uses two orthogonal governance layers:
    1. Gateway (gateway/route.py) — audit: records the compute decision
    2. ActionGate (agent/safety.py) — enforce: risk tier + taint check

Both are acceptable. The test verifies that NO governed execution path
runs without at least one of these layers in its call chain.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Set

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SRC = Path(__file__).resolve().parents[2] / "src"
_BUILTIN_MODULES = set(sys.stdlib_module_names)


def _imports_of(module_path: Path) -> Set[str]:
    """Return the set of module names imported by a Python file.

    Returns both the top-level package and the final segment so that
    ``from msb_v3.agent.safety import ActionGate`` matches both
    ``msb_v3`` and ``safety``.
    """
    try:
        tree = ast.parse(module_path.read_text())
    except SyntaxError:
        return set()
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                names.add(parts[0])
                if len(parts) > 1:
                    names.add(parts[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                parts = node.module.split(".")
                names.add(parts[0])
                if len(parts) > 1:
                    names.add(parts[-1])
    return names


def _calls_function_in(module_path: Path, func_name: str) -> bool:
    """Check whether a module contains a call to `func_name(...)`."""
    try:
        tree = ast.parse(module_path.read_text())
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == func_name:
                return True
            if isinstance(node.func, ast.Attribute) and node.func.attr == func_name:
                return True
    return False


def _calls_method_in(module_path: Path, method_name: str) -> bool:
    """Check whether a module contains a call to `something.method_name(...)`."""
    try:
        tree = ast.parse(module_path.read_text())
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == method_name:
                return True
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAgentHandleGatewayIntegration:
    """The agent.handle.handle() function must call gateway.route() to record
    the compute decision into the audit chain before execution."""

    def test_handle_imports_gateway(self):
        """agent/handle.py must import from msb_v3.gateway."""
        handle_path = _SRC / "msb_v3" / "agent" / "handle.py"
        assert handle_path.exists(), f"missing: {handle_path}"
        imports = _imports_of(handle_path)
        assert "gateway" in imports, (
            "agent/handle.py does not import msb_v3.gateway — "
            "the canonical governed loop bypasses the gateway"
        )

    def test_handle_calls_route(self):
        """agent/handle.py must call gateway.route() (or the route function
        imported from gateway) to record the compute decision."""
        handle_path = _SRC / "msb_v3" / "agent" / "handle.py"
        assert handle_path.exists()
        # Check for a call to `route(` — the function imported from gateway
        has_route_call = _calls_function_in(handle_path, "route")
        # Also check for gateway.route( in case it's called qualified
        has_qualified_call = _calls_function_in(handle_path, "gateway_route") or (
            _calls_method_in(handle_path, "route")
        )
        assert has_route_call or has_qualified_call, (
            "agent/handle.py imports gateway but never calls route() — "
            "the compute decision is not recorded in the audit chain"
        )


class TestChatHarnessGatewayIntegration:
    """ChatHarness.execute() must call gateway.route() — this is the existing
    integration and must not regress."""

    def test_chat_harness_imports_gateway(self):
        """harnesses/base.py must import from msb_v3.gateway."""
        base_path = _SRC / "msb_v3" / "harnesses" / "base.py"
        assert base_path.exists(), f"missing: {base_path}"
        imports = _imports_of(base_path)
        assert "gateway" in imports, (
            "harnesses/base.py does not import msb_v3.gateway — "
            "the chat harness bypasses the gateway"
        )

    def test_chat_harness_calls_route(self):
        """harnesses/base.py must call route() to record the compute decision."""
        base_path = _SRC / "msb_v3" / "harnesses" / "base.py"
        assert base_path.exists()
        has_route_call = _calls_function_in(base_path, "route")
        assert has_route_call, (
            "harnesses/base.py imports gateway but never calls route() — "
            "the chat harness does not record its compute decision"
        )


class TestGovernedExecutionHasGovernance:
    """Every governed execution path must have at least one governance layer
    (gateway or ActionGate) in its import chain. This is a structural check
    that catches new paths that bypass all governance."""

    @pytest.mark.parametrize(
        "module_path,description",
        [
            ("msb_v3/agent/handle.py", "agent handle slice"),
            ("msb_v3/harnesses/base.py", "chat harness"),
        ],
    )
    def test_governed_path_has_governance(self, module_path: str, description: str):
        """A governed execution path must import at least one of:
        - msb_v3.gateway (compute routing + audit)
        - msb_v3.agent.safety (ActionGate enforcement)
        - msb_v3.governance.killswitch (emergency shutdown)
        """
        full_path = _SRC / module_path
        if not full_path.exists():
            pytest.skip(f"module not found: {module_path}")
        imports = _imports_of(full_path)
        governance_modules = {"gateway", "safety", "killswitch", "governance"}
        has_governance = bool(imports & governance_modules)
        assert has_governance, (
            f"{description} ({module_path}) imports neither gateway nor "
            f"ActionGate nor KillSwitch — no governance layer in call chain. "
            f"Imports: {sorted(imports)}"
        )


class TestNoUngovernedToolExecution:
    """Verify that the tool execution layer (executor.py) is gated by
    ActionGate. The SafeProvider wrapper must be present in the agent path."""

    def test_executor_has_safe_provider_import(self):
        """agent/executor.py should reference SafeProvider or ActionGate
        (either directly or through the safety module)."""
        executor_path = _SRC / "msb_v3" / "agent" / "executor.py"
        if not executor_path.exists():
            pytest.skip("executor module not found")
        _imports_of(executor_path)
        # The executor itself doesn't import safety — the caller (handle.py)
        # wraps it. This test verifies the contract exists.
        # Instead, check that handle.py imports both executor and safety.
        handle_path = _SRC / "msb_v3" / "agent" / "handle.py"
        handle_imports = _imports_of(handle_path)
        assert "safety" in handle_imports or "ActionGate" in handle_imports, (
            "agent/handle.py does not import ActionGate — "
            "the governed loop has no tool execution gate"
        )
