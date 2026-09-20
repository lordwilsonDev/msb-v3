"""Identity shadow — observing I6 without enforcing it (Deliverable 02 §10).

The property under test is NOT "identity enforcement works" (it does not exist
yet). It is the two things shadow mode must guarantee:

    * a would-be verdict is produced, persisted, and honest about UNKNOWN; and
    * **no decision anywhere changes because of it** — the same tool call
      returns the same string and leaves the same audit trail with observation
      on or off.

The second is the load-bearing test. A shadow implementation that quietly
refuses one call is enforcement, and enforcement was not signed off.
"""

from __future__ import annotations

import pytest

from msb_v3.agent.identity import AgentIdentity, AgentRegistry
from msb_v3.core.config import settings
from msb_v3.governance import identity_shadow
from msb_v3.governance.identity_shadow import (
    ORIGIN_RUNTIME,
    ORIGIN_TEST,
    SURFACE_IN_PROCESS,
    IdentityShadowRecorder,
    evaluate_identity,
    run_origin,
    shadow_identity_decision,
)
from msb_v3.tools.runtime import _run_governed, register_governed_tools
from msb_v3.uac.audit_chain import AuditChain


@pytest.fixture()
def chain(monkeypatch, tmp_path):
    audit = AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)
    monkeypatch.setattr("msb_v3.uac.chain_anchor.anchored_chain_from_env", lambda: audit)
    return audit


@pytest.fixture()
def vault(monkeypatch, tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setattr(settings, "vault_path", str(root))
    return root


@pytest.fixture()
def registry(tmp_path):
    reg = AgentRegistry(str(tmp_path / "agents.db"))
    reg.register(
        AgentIdentity(
            agent_id="agent.live",
            name="live agent",
            kind="local",
            provider_id="local.slice",
            granted_capabilities=("vault.write",),
            tenant_scope="t1",
            max_risk_tier=2,
        )
    )
    reg.register(
        AgentIdentity(
            agent_id="agent.revoked",
            name="revoked agent",
            kind="local",
            provider_id="local.slice",
            tenant_scope="*",
            max_risk_tier=4,
        )
    )
    reg.revoke("agent.revoked")
    return reg


@pytest.fixture()
def recorder(monkeypatch, tmp_path, registry):
    """A tmp-rooted recorder bound to a tmp registry — never the live database."""
    rec = IdentityShadowRecorder(shadow_root=tmp_path / "shadow", registry=registry)
    monkeypatch.setattr(identity_shadow, "_default_recorder", rec)
    return rec


def _verdicts(chain: AuditChain) -> list[str]:
    return [r.payload.get("verdict", "<missing>") for r in chain.get_chain(component="tools")]


class _RecordingClient:
    def __init__(self) -> None:
        self.tools: dict = {}

    def register_tool(self, name: str, fn) -> None:
        self.tools[name] = fn


# --- the guards, evaluated but never applied -------------------------------


def test_no_actor_is_rejected_and_the_candidate_is_only_a_probe(registry):
    """The honest baseline: no caller supplies an actor today."""
    verdict = evaluate_identity(
        None, tenant="t", capability="read_vault", registry=registry, candidate_agent_id="chat"
    )
    assert verdict.kernel_state == "REJECTED"
    assert verdict.decision_value == "BLOCK"
    assert verdict.actor_id is None
    assert verdict.candidate_agent_id == "chat"
    assert verdict.candidate_found is False  # nothing registered under that name
    assert any("probe" in n for n in verdict.notes)


def test_candidate_probe_reports_a_registered_identity(registry):
    verdict = evaluate_identity(
        None, tenant="t1", capability="read_vault", registry=registry, candidate_agent_id="agent.live"
    )
    assert verdict.kernel_state == "REJECTED"  # still no actor
    assert verdict.candidate_found is True  # but a candidate exists
    assert verdict.candidate_revoked is False


def test_unknown_actor_is_rejected(registry):
    verdict = evaluate_identity("nobody", tenant="t", capability="read_vault", registry=registry)
    assert verdict.kernel_state == "REJECTED"
    assert "unknown actor" in verdict.reason


def test_revoked_identity_is_rejected(registry):
    verdict = evaluate_identity("agent.revoked", tenant="t", capability="read_vault", registry=registry)
    assert verdict.kernel_state == "REJECTED"
    assert verdict.reason == "actor is revoked"


def test_tenant_scope_mismatch_is_denied(registry):
    verdict = evaluate_identity("agent.live", tenant="other", capability="read_vault", registry=registry)
    assert verdict.kernel_state == "DENIED"
    assert verdict.tenant_scope == "t1"


def test_tenant_scope_wildcard_admits_any_tenant(tmp_path):
    reg = AgentRegistry(str(tmp_path / "agents.db"))
    reg.register(
        AgentIdentity(
            agent_id="a", name="a", kind="local", provider_id="p", tenant_scope="*", max_risk_tier=2
        )
    )
    verdict = evaluate_identity("a", tenant="whatever", capability="read_vault", registry=reg)
    assert verdict.kernel_state == "ALLOW"


def test_tier_above_actor_ceiling_is_denied(registry):
    """vault_delete is tier 3 in RISK_TIERS; agent.live's ceiling is 2."""
    verdict = evaluate_identity("agent.live", tenant="t1", capability="vault_delete", registry=registry)
    assert verdict.kernel_state == "DENIED"
    assert verdict.tier == 3
    assert verdict.max_risk_tier == 2


def test_capability_absent_from_risk_table_is_now_evaluable_via_declared_risk_class(registry):
    """vault.write is executed by tools in tools/registry.py and is absent from
    RISK_TIERS. It now resolves to tier 3 — the maximum over its declaring tools'
    declared risk_class (vault_promote_draft is HIGH) — with the provenance
    recorded, so guard 7 can actually be evaluated."""
    verdict = evaluate_identity("agent.live", tenant="t1", capability="vault.write", registry=registry)
    assert verdict.tier == 3
    assert verdict.tier_source == "declared_risk_class"
    assert verdict.kernel_state == "DENIED"  # ceiling 2 < tier 3
    assert any("declared risk_class" in n or "risk_class" in n for n in verdict.notes)


def test_derived_tier_respects_the_actor_ceiling_when_high_enough(tmp_path):
    reg = AgentRegistry(str(tmp_path / "agents.db"))
    reg.register(
        AgentIdentity(
            agent_id="a", name="a", kind="local", provider_id="p", tenant_scope="*", max_risk_tier=3
        )
    )
    verdict = evaluate_identity("a", tenant="t", capability="vault.write", registry=reg)
    assert verdict.tier == 3
    assert verdict.kernel_state == "ALLOW"


def test_capability_known_to_neither_table_is_still_unknown(registry):
    """Exhausting the options must never look like 'safe'."""
    verdict = evaluate_identity("agent.live", tenant="t1", capability="escalate.godmode", registry=registry)
    assert verdict.tier is None
    assert verdict.tier_source == "unknown"
    assert verdict.kernel_state == "UNKNOWN"
    assert verdict.decision_value == "UNKNOWN"


def test_risk_table_tier_wins_over_derived_tier(registry):
    """An operator-set RISK_TIERS value is never overridden by a derived one."""
    verdict = evaluate_identity("agent.live", tenant="t1", capability="write_file", registry=registry)
    assert (verdict.tier, verdict.tier_source) == (2, "risk_table")


def test_tool_with_no_declared_capability_is_allow_not_unknown(registry):
    """A read tool declares no capability, so guard 7 is vacuous rather than
    undecidable. ALLOW here, unlike the case above where the tier is genuinely
    unknowable."""
    verdict = evaluate_identity("agent.live", tenant="t1", capability=None, registry=registry)
    assert verdict.kernel_state == "ALLOW"
    assert verdict.tier is None
    assert any("not applicable" in n for n in verdict.notes)


def test_unreadable_registry_is_unknown_not_a_denial():
    class _Broken:
        def get(self, agent_id):
            raise RuntimeError("db is on fire")

    verdict = evaluate_identity("a", tenant="t", capability="read_vault", registry=_Broken())
    assert verdict.kernel_state == "UNKNOWN"
    assert "registry unavailable" in verdict.reason


# --- persistence and the enable flag ---------------------------------------


def test_record_is_persisted_with_the_shadow_marker(recorder, monkeypatch):
    monkeypatch.delenv("MSB_IDENTITY_SHADOW", raising=False)
    recorder.record(
        surface="chat",
        tool_id="vault_read",
        tenant="t1",
        session="s",
        actor_id="agent.live",
        capability="read_vault",
        required_capabilities=(),
        declared_risk_class="LOW",
    )
    records = recorder.load()
    assert len(records) == 1
    rec = records[0]
    assert rec["enforcement"] == "shadow"
    assert rec["surface"] == "chat"
    assert rec["actor_supplied"] is True
    assert rec["kernel_state"] == "ALLOW"
    assert rec["actor_fingerprint"]  # the drift-detectable identity hash


def test_disabled_flag_writes_nothing(recorder, monkeypatch, vault):
    monkeypatch.setenv("MSB_IDENTITY_SHADOW", "0")
    shadow_identity_decision(surface="chat", tool_id="vault_read", tenant="t", session="s")
    assert recorder.load() == []


def test_flag_is_read_live_so_it_can_be_flipped_without_a_restart(recorder, monkeypatch):
    monkeypatch.setenv("MSB_IDENTITY_SHADOW", "off")
    assert identity_shadow.shadow_enabled() is False
    monkeypatch.setenv("MSB_IDENTITY_SHADOW", "1")
    assert identity_shadow.shadow_enabled() is True
    monkeypatch.delenv("MSB_IDENTITY_SHADOW", raising=False)
    assert identity_shadow.shadow_enabled() is True  # unset = observe


def test_observation_never_raises_even_when_persistence_is_broken(monkeypatch, tmp_path):
    """Observation must never be able to fail a request."""

    class _Exploding(IdentityShadowRecorder):
        def _persist(self, record):
            raise OSError("disk full")

    monkeypatch.setattr(identity_shadow, "_default_recorder", _Exploding(shadow_root=tmp_path / "shadow"))
    monkeypatch.delenv("MSB_IDENTITY_SHADOW", raising=False)
    shadow_identity_decision(surface="chat", tool_id="vault_read", tenant="t", session="s")


# --- the load-bearing test: nothing changes --------------------------------


def test_mcp_bridge_constants_exist_on_the_module():
    """Regression guard for a real slip made while wiring this up.

    The bridge's grant constant was deleted while editing its neighbourhood.
    The suite stayed green anyway, because ``tests/api/test_mcp_security.py``
    monkeypatches that attribute with ``raising=False`` — which *creates* it
    when it is absent, so the one test that should have caught a missing module
    constant silently supplied it. The production call path raised NameError.

    A module constant the live path reads must be asserted, not assumed.
    """
    from msb_v3.api import mcp_bridge

    assert isinstance(mcp_bridge._MCP_GRANTED_CAPABILITIES, frozenset)
    assert mcp_bridge._MCP_ACTOR_ID is None or isinstance(mcp_bridge._MCP_ACTOR_ID, str)


def test_shadow_changes_no_decision_and_no_audit_trail(chain, vault, recorder, monkeypatch):
    (vault / "note.txt").write_text("hello sovereign")

    monkeypatch.setenv("MSB_IDENTITY_SHADOW", "0")
    off = _run_governed("vault_read", {"path": "note.txt"}, granted=frozenset(), tenant="t", session="s")
    assert recorder.load() == []
    assert _verdicts(chain) == ["allowed"]

    monkeypatch.setenv("MSB_IDENTITY_SHADOW", "1")
    on = _run_governed("vault_read", {"path": "note.txt"}, granted=frozenset(), tenant="t", session="s")

    assert on == off
    assert "hello sovereign" in on
    assert _verdicts(chain) == ["allowed", "allowed"]
    assert len(recorder.load()) == 1  # observed exactly once, nothing else moved


def test_denied_calls_are_not_observed_by_design(chain, vault, recorder, monkeypatch):
    """Observation sits after the existing gates, so refusals are untouched.
    The question the data answers is 'of the calls that execute today, how many
    would an identity requirement stop?' — a call nobody allowed is not that."""
    monkeypatch.delenv("MSB_IDENTITY_SHADOW", raising=False)
    result = _run_governed(
        "vault_write", {"path": "x.md", "content": "no"}, granted=frozenset(), tenant="t", session="s"
    )
    assert result == "[denied] tool vault_write requires capabilities: vault.write"
    assert recorder.load() == []


def test_registration_forwards_actor_and_surface_into_the_record(chain, vault, recorder, monkeypatch):
    monkeypatch.delenv("MSB_IDENTITY_SHADOW", raising=False)
    (vault / "note.txt").write_text("hello sovereign")
    client = _RecordingClient()
    register_governed_tools(
        client,
        {
            "tools": [{"name": "vault_read"}],
            "session": "s",
            "surface": "chat",
            "actor_id": "agent.live",
            "tenant": "t1",
        },
    )
    assert "hello sovereign" in client.tools["vault_read"](path="note.txt")

    rec = recorder.load()[-1]
    assert rec["surface"] == "chat"
    assert rec["actor_id"] == "agent.live"
    assert rec["actor_supplied"] is True
    assert rec["kernel_state"] == "ALLOW"
    assert rec["tool_id"] == "vault_read"


def test_blank_or_non_string_actor_is_treated_as_no_actor(chain, vault, recorder, monkeypatch):
    """The actor arrives as untrusted input; a blank value must not read as an
    identity, and must not be coerced into one."""
    monkeypatch.delenv("MSB_IDENTITY_SHADOW", raising=False)
    (vault / "note.txt").write_text("hello sovereign")
    for supplied in ("", "   ", 7, None, {"agent_id": "agent.live"}):
        client = _RecordingClient()
        register_governed_tools(
            client, {"tools": [{"name": "vault_read"}], "session": "s", "surface": "chat", "actor_id": supplied}
        )
        client.tools["vault_read"](path="note.txt")

    records = recorder.load()
    assert len(records) == 5
    assert all(r["actor_supplied"] is False for r in records)
    assert all(r["kernel_state"] == "REJECTED" for r in records)


# --- entry path: no call may be left unattributable ------------------------

def test_a_caller_that_names_no_surface_is_recorded_as_in_process(chain, vault, recorder, monkeypatch):
    """The old default was the sentinel "unknown", which said only that nobody
    had filled the field in — so no report could tell an in-process caller from a
    reporting bug. A caller that names nothing now says something true."""
    monkeypatch.delenv("MSB_IDENTITY_SHADOW", raising=False)
    (vault / "note.txt").write_text("hello sovereign")
    client = _RecordingClient()
    register_governed_tools(client, {"tools": [{"name": "vault_read"}], "session": "s"})
    client.tools["vault_read"](path="note.txt")

    assert recorder.load()[-1]["surface"] == SURFACE_IN_PROCESS


def test_a_declared_surface_reaches_the_record(chain, vault, recorder, monkeypatch):
    monkeypatch.delenv("MSB_IDENTITY_SHADOW", raising=False)
    (vault / "note.txt").write_text("hello sovereign")
    for name in ("moie", "context-engine", "factory", "codegraph", "memory-fabric", "governed-loop"):
        client = _RecordingClient()
        register_governed_tools(
            client, {"tools": [{"name": "vault_read"}], "session": "s", "surface": name}
        )
        client.tools["vault_read"](path="note.txt")

    assert [r["surface"] for r in recorder.load()] == [
        "moie",
        "context-engine",
        "factory",
        "codegraph",
        "memory-fabric",
        "governed-loop",
    ]


def test_blank_or_non_string_surface_is_treated_as_undeclared(chain, vault, recorder, monkeypatch):
    """Same fail-closed reading as the actor: a label that reaches the corpus
    must be one a caller chose, never a coerced value."""
    monkeypatch.delenv("MSB_IDENTITY_SHADOW", raising=False)
    (vault / "note.txt").write_text("hello sovereign")
    for supplied in ("", "   ", 7, None, ["chat"]):
        client = _RecordingClient()
        register_governed_tools(
            client, {"tools": [{"name": "vault_read"}], "session": "s", "surface": supplied}
        )
        client.tools["vault_read"](path="note.txt")

    assert all(r["surface"] == SURFACE_IN_PROCESS for r in recorder.load())


# --- origin: test traffic is not field evidence ----------------------------

def test_run_origin_detects_a_test_run(monkeypatch):
    monkeypatch.delenv("MSB_IDENTITY_SHADOW_ORIGIN", raising=False)
    # pytest sets PYTEST_CURRENT_TEST for every phase of a test, so a suite run
    # labels its own traffic without any call site having to remember.
    assert run_origin() == ORIGIN_TEST
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert run_origin() == ORIGIN_RUNTIME


def test_run_origin_honours_an_explicit_declaration(monkeypatch):
    monkeypatch.setenv("MSB_IDENTITY_SHADOW_ORIGIN", "runtime")
    assert run_origin() == ORIGIN_RUNTIME
    # A custom label is honoured verbatim and shows up in its own bucket, rather
    # than being silently discarded in favour of the heuristic.
    monkeypatch.setenv("MSB_IDENTITY_SHADOW_ORIGIN", "canary")
    assert run_origin() == "canary"
    monkeypatch.setenv("MSB_IDENTITY_SHADOW_ORIGIN", "   ")
    assert run_origin() == ORIGIN_TEST  # blank declaration = no declaration


def test_only_a_live_surface_is_probed_as_a_bootstrap_candidate(chain, vault, recorder, monkeypatch):
    """`candidate_agent_id` falls back to the surface name. Asking whether an
    identity literally called "moie" is registered is not a criterion-4 question
    — it is the sentinel leak of JOB-018 one level up, and naming the entry paths
    is what made it visible."""
    monkeypatch.delenv("MSB_IDENTITY_SHADOW", raising=False)
    monkeypatch.delenv("MSB_IDENTITY_SHADOW_CANDIDATE", raising=False)
    (vault / "note.txt").write_text("hello sovereign")
    for name in ("chat", "moie"):
        client = _RecordingClient()
        register_governed_tools(
            client, {"tools": [{"name": "vault_read"}], "session": "s", "surface": name}
        )
        client.tools["vault_read"](path="note.txt")

    live_rec, in_process_rec = recorder.load()[-2:]
    assert live_rec["surface"] == "chat" and live_rec["candidate_agent_id"] == "chat"
    assert in_process_rec["surface"] == "moie" and in_process_rec["candidate_agent_id"] is None

    # an explicit declaration still probes, whatever the surface
    monkeypatch.setenv("MSB_IDENTITY_SHADOW_CANDIDATE", "candidate.agent")
    client = _RecordingClient()
    register_governed_tools(
        client, {"tools": [{"name": "vault_read"}], "session": "s", "surface": "moie"}
    )
    client.tools["vault_read"](path="note.txt")
    assert recorder.load()[-1]["candidate_agent_id"] == "candidate.agent"


def test_records_carry_the_origin(chain, vault, recorder, monkeypatch):
    monkeypatch.delenv("MSB_IDENTITY_SHADOW", raising=False)
    monkeypatch.delenv("MSB_IDENTITY_SHADOW_ORIGIN", raising=False)
    (vault / "note.txt").write_text("hello sovereign")
    _run_governed("vault_read", {"path": "note.txt"}, granted=frozenset(), tenant="t", session="s")
    assert recorder.load()[-1]["origin"] == ORIGIN_TEST

    monkeypatch.setenv("MSB_IDENTITY_SHADOW_ORIGIN", "runtime")
    _run_governed("vault_read", {"path": "note.txt"}, granted=frozenset(), tenant="t", session="s")
    assert recorder.load()[-1]["origin"] == ORIGIN_RUNTIME
