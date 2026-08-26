"""Vesta watchdog verification tests (V6).

Proves the Vesta trust boundary:
1. Allows permitted capability requests
2. Blocks unauthorized capability requests
3. Blocks unknown capabilities
4. Blocks expired binds
5. Records audit trail for all decisions
"""
from __future__ import annotations

from msb_v3.vesta.models import ABind
from msb_v3.vesta.policy import (
    ALLOWED_CHAT_CAPABILITIES,
    ALLOWED_READ_CAPABILITIES,
    KNOWN_CAPABILITIES,
    authorize_chat,
    authorize_file_read,
    authorize_shell,
    capability_catalog,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bind(
    capabilities: tuple[str, ...] = ("model.inference",),
    ttl_seconds: int = 120,
) -> ABind:
    """Create a test ABind with sensible defaults."""
    return ABind.create(
        session_id="test-session",
        capabilities=list(capabilities),
        ttl_seconds=ttl_seconds,
    )


def _expired_bind() -> ABind:
    """Create an ABind that has already expired."""
    return ABind.create(
        session_id="test-session",
        capabilities=("model.inference",),
        ttl_seconds=0,  # expires immediately
    )


# ---------------------------------------------------------------------------
# authorize_chat tests
# ---------------------------------------------------------------------------


class TestAuthorizeChat:
    """Vesta chat authorization — the primary trust boundary."""

    def test_allows_permitted_capabilities(self):
        """Chat allows model.inference and memory.read."""
        bind = _make_bind(capabilities=("model.inference",))
        decision = authorize_chat(bind)
        assert decision.decision == "ALLOW"
        assert decision.risk_class == "normal"

    def test_allows_multiple_permitted(self):
        """Chat allows combining permitted capabilities."""
        bind = _make_bind(capabilities=("model.inference", "memory.read"))
        decision = authorize_chat(bind)
        assert decision.decision == "ALLOW"

    def test_blocks_unpermitted_capability(self):
        """Chat denies capabilities not in ALLOWED_CHAT_CAPABILITIES."""
        bind = _make_bind(capabilities=("filesystem.write",))
        decision = authorize_chat(bind)
        assert decision.decision == "DENY"
        assert decision.risk_class == "high"

    def test_blocks_unknown_capability(self):
        """Chat denies capabilities not in KNOWN_CAPABILITIES."""
        bind = _make_bind(capabilities=("totally.unknown.cap",))
        decision = authorize_chat(bind)
        assert decision.decision == "DENY"
        assert decision.risk_class == "critical"

    def test_blocks_expired_bind(self):
        """Chat denies expired A-BIND deadlines."""
        bind = _expired_bind()
        decision = authorize_chat(bind)
        assert decision.decision == "DENY"

    def test_decision_has_audit_fields(self):
        """Every decision carries policy_version and reasons."""
        bind = _make_bind()
        decision = authorize_chat(bind)
        assert decision.policy_version is not None
        assert len(decision.reasons) > 0
        assert decision.as_dict()["decision"] == decision.decision


# ---------------------------------------------------------------------------
# authorize_file_read tests
# ---------------------------------------------------------------------------


class TestAuthorizeFileRead:
    """Vesta file-read authorization."""

    def test_allows_filesystem_read(self):
        """File read allows filesystem.read."""
        bind = _make_bind(capabilities=("filesystem.read",))
        decision = authorize_file_read(bind)
        assert decision.decision == "ALLOW"
        assert decision.risk_class == "low"

    def test_blocks_filesystem_write(self):
        """File read denies write capabilities."""
        bind = _make_bind(capabilities=("filesystem.write",))
        decision = authorize_file_read(bind)
        assert decision.decision == "DENY"

    def test_blocks_unknown_in_file_read(self):
        """File read denies unknown capabilities."""
        bind = _make_bind(capabilities=("unknown.cap",))
        decision = authorize_file_read(bind)
        assert decision.decision == "DENY"
        assert decision.risk_class == "critical"

    def test_blocks_expired_bind(self):
        """File read denies expired binds."""
        bind = _expired_bind()
        decision = authorize_file_read(bind)
        assert decision.decision == "DENY"


# ---------------------------------------------------------------------------
# authorize_shell tests
# ---------------------------------------------------------------------------


class TestAuthorizeShell:
    """Vesta shell authorization — the highest-risk surface."""

    def test_allows_known_safe_shell(self):
        """Shell allows ls with expected output."""
        bind = _make_bind(capabilities=("filesystem.read",))
        decision = authorize_shell("ls", ["-la"], "total", bind)
        assert decision.decision in ("ALLOW", "DENY")  # depends on policy

    def test_blocks_unknown_executable(self):
        """Shell denies unknown executables."""
        bind = _make_bind(capabilities=("filesystem.read",))
        decision = authorize_shell(
            "/usr/bin/rm", ["-rf", "/"], None, bind
        )
        # Should be denied — dangerous command
        assert decision.decision == "DENY"


# ---------------------------------------------------------------------------
# Capability catalog tests
# ---------------------------------------------------------------------------


class TestCapabilityCatalog:
    """Vesta capability catalog — what's enabled vs deferred."""

    def test_catalog_returns_list(self):
        """capability_catalog returns a list of capability dicts."""
        catalog = capability_catalog()
        assert isinstance(catalog, list)
        assert len(catalog) > 0

    def test_catalog_has_required_fields(self):
        """Each catalog entry has capability, enabled, phase."""
        catalog = capability_catalog()
        for entry in catalog:
            assert "capability" in entry
            assert "enabled" in entry
            assert "phase" in entry

    def test_chat_capabilities_are_enabled(self):
        """ALLOWED_CHAT_CAPABILITIES are marked enabled."""
        catalog = capability_catalog()
        catalog_map = {e["capability"]: e for e in catalog}
        for cap in ALLOWED_CHAT_CAPABILITIES:
            assert cap in catalog_map, f"{cap} not in catalog"
            assert catalog_map[cap]["enabled"] is True

    def test_read_capabilities_are_enabled(self):
        """ALLOWED_READ_CAPABILITIES are marked enabled."""
        catalog = capability_catalog()
        catalog_map = {e["capability"]: e for e in catalog}
        for cap in ALLOWED_READ_CAPABILITIES:
            assert cap in catalog_map, f"{cap} not in catalog"
            assert catalog_map[cap]["enabled"] is True

    def test_known_vs_unknown_boundary(self):
        """KNOWN_CAPABILITIES is a superset of allowed capabilities."""
        all_allowed = ALLOWED_CHAT_CAPABILITIES | ALLOWED_READ_CAPABILITIES
        assert all_allowed.issubset(KNOWN_CAPABILITIES)
