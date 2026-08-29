"""Import direction enforcement (convergence blueprint §29).

Enforces that the dependency direction follows the architecture:

    contracts
       ↑
    adapters
       ↑
    gateway
       ↑
    runtime

The test verifies that lower-level modules do NOT import from higher-level
modules. This catches circular dependencies and architectural inversions.

Specifically:
- `agent/contract.py` must NOT import from `agent/providers.py`
  (contracts are the boundary, not the implementation)
- `gateway/route.py` must NOT import from `agent/` (gateway is below agent)
- `evidence/spine.py` must NOT import from `agent/` (evidence is below agent)
- `governance/` must NOT import from `agent/` (governance is below agent)
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Set

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"


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


# ---------------------------------------------------------------------------
# Import direction rules
# ---------------------------------------------------------------------------


class TestContractBoundary:
    """contract.py is the boundary definition. It must NOT import from
    providers.py (the implementation). This keeps the contract pure."""

    def test_contract_does_not_import_providers(self):
        """agent/contract.py must not import from agent/providers.py."""
        contract_path = _SRC / "msb_v3" / "agent" / "contract.py"
        if not contract_path.exists():
            pytest.skip("contract.py not found")
        imports = _imports_of(contract_path)
        assert "providers" not in imports, (
            "agent/contract.py imports agent/providers.py — "
            "the contract boundary is violated"
        )

    def test_contract_does_not_import_handle(self):
        """agent/contract.py must not import from agent/handle.py."""
        contract_path = _SRC / "msb_v3" / "agent" / "contract.py"
        if not contract_path.exists():
            pytest.skip("contract.py not found")
        imports = _imports_of(contract_path)
        assert "handle" not in imports, (
            "agent/contract.py imports agent/handle.py — "
            "the contract boundary is violated"
        )


class TestGatewayBelowAgent:
    """gateway/ is below agent/ in the dependency graph. The gateway
    must NOT import from agent/ (it's a lower-level routing layer)."""

    def test_gateway_does_not_import_agent(self):
        """gateway/route.py must not import from agent/."""
        gateway_path = _SRC / "msb_v3" / "gateway" / "route.py"
        if not gateway_path.exists():
            pytest.skip("gateway/route.py not found")
        imports = _imports_of(gateway_path)
        # 'agent' as a top-level import would mean gateway depends on agent
        agent_imports = {i for i in imports if i in ("agent", "handle", "safety", "providers")}
        assert not agent_imports, (
            f"gateway/route.py imports from agent/ ({agent_imports}) — "
            f"the dependency direction is inverted"
        )


class TestEvidenceBelowAgent:
    """evidence/ is below agent/ in the dependency graph. The evidence
    spine must NOT import from agent/ (it's a lower-level provenance layer)."""

    def test_spine_does_not_import_agent(self):
        """evidence/spine.py must not import from agent/."""
        spine_path = _SRC / "msb_v3" / "evidence" / "spine.py"
        if not spine_path.exists():
            pytest.skip("evidence/spine.py not found")
        imports = _imports_of(spine_path)
        agent_imports = {i for i in imports if i in ("agent", "handle", "safety", "providers")}
        assert not agent_imports, (
            f"evidence/spine.py imports from agent/ ({agent_imports}) — "
            f"the dependency direction is inverted"
        )


class TestGovernanceBelowAgent:
    """governance/ is below agent/ in the dependency graph. The governance
    layer must NOT import from agent/ (it's a lower-level enforcement layer)."""

    def test_killswitch_does_not_import_agent(self):
        """governance/killswitch.py must not import from agent/."""
        ks_path = _SRC / "msb_v3" / "governance" / "killswitch.py"
        if not ks_path.exists():
            pytest.skip("governance/killswitch.py not found")
        imports = _imports_of(ks_path)
        agent_imports = {i for i in imports if i in ("agent", "handle", "safety", "providers")}
        assert not agent_imports, (
            f"governance/killswitch.py imports from agent/ ({agent_imports}) — "
            f"the dependency direction is inverted"
        )


class TestNoCircularImports:
    """Verify that key modules don't create import cycles."""

    def test_providers_does_not_import_contract(self):
        """providers.py should not import contract.py (contract is the
        boundary, providers implement it)."""
        providers_path = _SRC / "msb_v3" / "agent" / "providers.py"
        if not providers_path.exists():
            pytest.skip("providers.py not found")
        imports = _imports_of(providers_path)
        # It's OK for providers to import contract for type annotations,
        # but not for runtime logic
        assert "contract" not in imports, (
            "agent/providers.py imports agent/contract.py — "
            "this may create a circular dependency"
        )
