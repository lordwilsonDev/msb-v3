"""Gateway routing verification tests.

Proves the gateway:
1. Allows calls with matching capabilities
2. Denies calls with missing capabilities
3. Denies calls requiring authorization when not granted
4. Routes small calls to local backend
5. Routes large calls to frontier
6. Every decision is audit-logged
"""
from __future__ import annotations

from typing import Any, FrozenSet

from msb_v3.gateway.route import (
    BACKEND_FRONTIER,
    GatewayCall,
    GatewayContext,
    route,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_call(
    name: str = "llm.infer",
    capabilities: FrozenSet[str] = frozenset(),
    requires_authorization: bool = False,
    estimated_bytes: int = 1000,
    **kwargs: Any,
) -> GatewayCall:
    """Create a test GatewayCall."""
    return GatewayCall(
        name=name,
        capabilities=capabilities,
        requires_authorization=requires_authorization,
        estimated_bytes=estimated_bytes,
        **kwargs,
    )


def _make_ctx(
    capabilities: FrozenSet[str] = frozenset(),
    authorizations: FrozenSet[str] = frozenset(),
    budget: int = 8 * 1024 * 1024 * 1024,  # 8GB default
) -> GatewayContext:
    """Create a test GatewayContext."""
    return GatewayContext(
        granted_capabilities=capabilities,
        granted_authorizations=authorizations,
        local_budget_bytes=budget,
    )


# ---------------------------------------------------------------------------
# Capability gate tests
# ---------------------------------------------------------------------------


class TestGatewayCapabilities:
    """Gateway capability gate — the primary routing decision."""

    def test_allows_when_capabilities_match(self):
        """Call with matching capabilities is authorized."""
        call = _make_call(capabilities=frozenset({"llm.infer"}))
        ctx = _make_ctx(capabilities=frozenset({"llm.infer", "tool.shell"}))
        decision = route(call, ctx)
        assert decision.authorized is True

    def test_denies_when_capabilities_missing(self):
        """Call with missing capabilities is denied."""
        call = _make_call(capabilities=frozenset({"tool.shell"}))
        ctx = _make_ctx(capabilities=frozenset({"llm.infer"}))
        decision = route(call, ctx)
        assert decision.authorized is False
        assert "missing_capabilities" in decision.reason

    def test_allows_when_no_capabilities_required(self):
        """Call with no capability requirements is always authorized."""
        call = _make_call(capabilities=frozenset())
        ctx = _make_ctx(capabilities=frozenset())
        decision = route(call, ctx)
        assert decision.authorized is True


# ---------------------------------------------------------------------------
# Authorization gate tests
# ---------------------------------------------------------------------------


class TestGatewayAuthorization:
    """Gateway authorization gate — Experimental Plane §5."""

    def test_allows_when_authorization_granted(self):
        """Call with granted authorization is authorized."""
        call = _make_call(
            name="experiment.intervene",
            requires_authorization=True,
            metadata={"slug": "exp-001"},
        )
        ctx = _make_ctx(
            authorizations=frozenset({"experiment.intervene:exp-001"}),
        )
        decision = route(call, ctx)
        assert decision.authorized is True

    def test_denies_when_authorization_missing(self):
        """Call requiring authorization without grant is denied."""
        call = _make_call(
            name="experiment.intervene",
            requires_authorization=True,
        )
        ctx = _make_ctx()  # no authorizations
        decision = route(call, ctx)
        assert decision.authorized is False
        assert "requires_authorization_not_granted" in decision.reason

    def test_allows_non_authorization_calls(self):
        """Calls not requiring authorization skip the auth gate."""
        call = _make_call(
            name="llm.infer",
            requires_authorization=False,
        )
        ctx = _make_ctx()
        decision = route(call, ctx)
        assert decision.authorized is True


# ---------------------------------------------------------------------------
# Backend selection tests
# ---------------------------------------------------------------------------


class TestGatewayBackendSelection:
    """Gateway backend selection — Compute Plane §3."""

    def test_small_call_routes_local(self):
        """Small calls route to local backend."""
        call = _make_call(estimated_bytes=1000)
        ctx = _make_ctx(budget=8 * 1024 * 1024 * 1024)
        decision = route(call, ctx)
        assert decision.authorized is True
        assert decision.backend is not None
        assert "local" in decision.backend.lower() or "ollama" in decision.backend.lower()

    def test_large_call_routes_frontier(self):
        """Large calls route to frontier backend."""
        call = _make_call(estimated_bytes=9 * 1024 * 1024 * 1024)  # 9GB
        ctx = _make_ctx(budget=8 * 1024 * 1024 * 1024)  # 8GB budget
        decision = route(call, ctx)
        assert decision.authorized is True
        assert decision.backend == BACKEND_FRONTIER


# ---------------------------------------------------------------------------
# Audit trail tests
# ---------------------------------------------------------------------------


class TestGatewayAuditTrail:
    """Gateway audit trail — every decision is recorded."""

    def test_decision_has_id(self):
        """Every decision has a decision_id (audit chain hash)."""
        call = _make_call()
        ctx = _make_ctx()
        decision = route(call, ctx)
        assert decision.decision_id is not None
        assert len(decision.decision_id) > 0

    def test_denied_decision_has_id(self):
        """Denied decisions also have audit IDs."""
        call = _make_call(capabilities=frozenset({"missing.cap"}))
        ctx = _make_ctx()
        decision = route(call, ctx)
        assert decision.authorized is False
        assert decision.decision_id is not None

    def test_decision_has_reason(self):
        """Every decision includes a human-readable reason."""
        call = _make_call()
        ctx = _make_ctx()
        decision = route(call, ctx)
        assert isinstance(decision.reason, str)
        assert len(decision.reason) > 0
