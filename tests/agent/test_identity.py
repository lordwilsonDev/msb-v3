"""Agent identity (unified-architecture §17): durable, capability-scoped grants.

An agent does only what its registered identity grants. The fingerprint is
content-addressed over the authorization-relevant fields, so a drifted grant
is detectable.
"""

from __future__ import annotations

import pytest

from msb_v3.agent.identity import AgentIdentity, AgentRegistry, compute_fingerprint


@pytest.fixture()
def registry(tmp_path):
    return AgentRegistry(db_path=str(tmp_path / "agents.db"))


def _identity(agent_id: str = "agent_07", caps=("read_vault", "llm_synthesis")) -> AgentIdentity:
    return AgentIdentity(
        agent_id=agent_id,
        name="researcher",
        kind="local",
        provider_id="local.slice",
        granted_capabilities=caps,
        autonomy_level=2,
        max_risk_tier=2,
    )


def test_register_get_round_trip(registry):
    registry.register(_identity())
    got = registry.get("agent_07")
    assert got.agent_id == "agent_07"
    assert got.granted_capabilities == ("read_vault", "llm_synthesis")
    assert got.revoked is False


def test_fingerprint_is_stable_and_content_addressed(registry):
    a = _identity()
    b = _identity()
    assert a.fingerprint == b.fingerprint
    drifted = _identity(caps=("read_vault", "write_file"))
    assert drifted.fingerprint != a.fingerprint
    # computed independently of the instance
    assert a.fingerprint == compute_fingerprint(
        provider_id="local.slice",
        model="local",
        granted_capabilities=("read_vault", "llm_synthesis"),
        tenant_scope="*",
        autonomy_level=2,
        max_risk_tier=2,
    )


def test_revoke_blocks_capability(registry):
    registry.register(_identity())
    assert registry.has_capability("agent_07", "read_vault") is True
    registry.revoke("agent_07", "operator")
    got = registry.get("agent_07")
    assert got.revoked is True
    assert registry.has_capability("agent_07", "read_vault") is False
    assert got.has_capability("read_vault") is False


def test_unknown_agent_has_no_capability(registry):
    assert registry.has_capability("ghost", "read_vault") is False
    with pytest.raises(KeyError):
        registry.get("ghost")


def test_list_excludes_revoked_by_default(registry):
    registry.register(_identity("a"))
    registry.register(_identity("b"))
    registry.revoke("b")
    ids = {a["agent_id"] for a in registry.list()}
    assert ids == {"a"}
    assert {a["agent_id"] for a in registry.list(include_revoked=True)} == {"a", "b"}


def test_revocation_survives_reopen(tmp_path):
    db = str(tmp_path / "agents.db")
    AgentRegistry(db_path=db).register(_identity())
    AgentRegistry(db_path=db).revoke("agent_07")
    reopened = AgentRegistry(db_path=db)
    assert reopened.get("agent_07").revoked is True
    assert reopened.has_capability("agent_07", "read_vault") is False
