"""The chat surface's acting principal (Deliverable 02 §10, K22 criterion 1).

K22 criterion 1 asks that both live surfaces pass an actor so the identity
invariant can be evaluated from shadow data. This file pins the two things that
make that safe:

1. **Precedence.** Operator config beats anything in ``context``. If a caller
   could name its own actor on an enforcement path, I6 would be satisfied by
   self-authorization — the hole the kernel spec exists to close (§7, K11).
2. **It reaches the recorder.** A real ``ChatHarness.execute`` with a model that
   calls a tool lands a shadow record naming the configured actor.

Nothing here enforces anything; ``enforcement`` is asserted ``"shadow"``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from msb_v3.agent.identity import AgentIdentity, AgentRegistry
from msb_v3.core.config import settings
from msb_v3.governance import identity_shadow
from msb_v3.governance.identity_shadow import IdentityShadowRecorder
from msb_v3.harnesses import base as base_module
from msb_v3.harnesses.base import ChatHarness, chat_actor
from msb_v3.uac.audit_chain import AuditChain


@pytest.fixture()
def audit(monkeypatch, tmp_path):
    chain = AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)
    monkeypatch.setattr("msb_v3.uac.chain_anchor.anchored_chain_from_env", lambda: chain)
    return chain


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
            agent_id="chat.agent",
            name="chat surface",
            kind="local",
            provider_id="local.slice",
            tenant_scope="*",
            max_risk_tier=2,
        )
    )
    return reg


@pytest.fixture()
def recorder(monkeypatch, tmp_path, registry):
    rec = IdentityShadowRecorder(shadow_root=tmp_path / "shadow", registry=registry)
    monkeypatch.setattr(identity_shadow, "_default_recorder", rec)
    return rec


# --- the precedence rule ---------------------------------------------------


def test_unset_means_no_actor(monkeypatch):
    """Today's behaviour, preserved deliberately: unset asserts nothing."""
    monkeypatch.delenv(base_module.CHAT_ACTOR_ENV, raising=False)
    assert chat_actor({}) is None
    assert chat_actor(None) is None


def test_operator_config_beats_a_caller_supplied_actor(monkeypatch):
    """The security property. A caller must not be able to name itself when the
    operator has configured the surface's identity."""
    monkeypatch.setenv(base_module.CHAT_ACTOR_ENV, "chat.agent")
    assert chat_actor({"actor_id": "attacker.chosen"}) == "chat.agent"


def test_caller_actor_is_honoured_only_when_no_operator_identity_is_configured(monkeypatch):
    """In-process callers (execution_loop, bridge_provider) have no other way to
    be attributed."""
    monkeypatch.delenv(base_module.CHAT_ACTOR_ENV, raising=False)
    assert chat_actor({"actor_id": "in.process"}) == "in.process"


@pytest.mark.parametrize("value", ["", "   ", None, 7, {"a": 1}, b"bytes"])
def test_blank_or_non_string_values_are_no_actor(monkeypatch, value):
    monkeypatch.delenv(base_module.CHAT_ACTOR_ENV, raising=False)
    assert chat_actor({"actor_id": value}) is None


def test_blank_operator_config_falls_through_rather_than_asserting_an_empty_identity(monkeypatch):
    monkeypatch.setenv(base_module.CHAT_ACTOR_ENV, "   ")
    assert chat_actor({}) is None
    assert chat_actor({"actor_id": "in.process"}) == "in.process"


def test_operator_config_is_read_live_so_it_can_change_without_a_restart(monkeypatch):
    monkeypatch.delenv(base_module.CHAT_ACTOR_ENV, raising=False)
    assert chat_actor({}) is None
    monkeypatch.setenv(base_module.CHAT_ACTOR_ENV, "chat.agent")
    assert chat_actor({}) == "chat.agent"


# --- end to end through the real chat path ---------------------------------


@dataclass
class _Resp:
    text: str = "done"
    latency_s: float = 0.01
    model: str = "stub"
    prompt_tokens: int = 1
    completion_tokens: int = 1


@dataclass
class _Decision:
    authorized: bool = True
    decision_id: str = "stub-decision"
    reason: str = "stub"


class _FakeModelClient:
    """Stands in for the local model client. When the harness hands it governed
    tools, it calls one — which is what a model tool call does, and what routes a
    call through ``_run_governed`` where the shadow observes it."""

    def __init__(self) -> None:
        self.tools: dict = {}
        self.results: list = []

    def register_tool(self, name: str, fn) -> None:
        self.tools[name] = fn

    def execute_tool_loop(self, query, system=None, tools=None):
        if "vault_read" in self.tools:
            self.results.append(self.tools["vault_read"](path="note.txt"))
        return _Resp()


@pytest.fixture()
def stub_runtime(monkeypatch):
    monkeypatch.setattr(base_module, "route", lambda *a, **k: _Decision())
    monkeypatch.setattr(base_module, "active_backend", lambda: "stub")
    return _FakeModelClient()


def test_chat_path_asserts_the_configured_actor_end_to_end(
    vault, audit, recorder, stub_runtime, monkeypatch
):
    monkeypatch.setenv(base_module.CHAT_ACTOR_ENV, "chat.agent")
    (vault / "note.txt").write_text("hello sovereign")

    result = ChatHarness(client=stub_runtime).execute(
        "read the note", {"tools": [{"name": "vault_read"}]}, session="s"
    )

    assert result.ok is True
    assert stub_runtime.results and "hello sovereign" in stub_runtime.results[0]

    records = recorder.load()
    assert records, "the chat path produced no shadow record"
    rec = records[-1]
    assert rec["surface"] == "chat"
    assert rec["actor_id"] == "chat.agent"
    assert rec["actor_supplied"] is True
    assert rec["tool_id"] == "vault_read"
    assert rec["kernel_state"] == "ALLOW"  # registered, live, scope ok, ceiling ok
    assert rec["enforcement"] == "shadow"  # observed, never applied


def test_chat_path_with_no_configured_actor_records_the_honest_baseline(
    vault, audit, recorder, stub_runtime, monkeypatch
):
    monkeypatch.delenv(base_module.CHAT_ACTOR_ENV, raising=False)
    (vault / "note.txt").write_text("hello sovereign")

    ChatHarness(client=stub_runtime).execute(
        "read the note", {"tools": [{"name": "vault_read"}]}, session="s"
    )

    rec = recorder.load()[-1]
    assert rec["actor_supplied"] is False
    assert rec["kernel_state"] == "REJECTED"
    assert rec["reason"] == "no actor supplied by the caller"


def test_chat_path_with_an_unregistered_actor_records_rejection(
    vault, audit, recorder, stub_runtime, monkeypatch
):
    """Configuring an identity that was never registered must show up as such —
    that is the K22 criterion-4 evidence ('is a bootstrap identity justified?')."""
    monkeypatch.setenv(base_module.CHAT_ACTOR_ENV, "never.registered")
    (vault / "note.txt").write_text("hello sovereign")

    ChatHarness(client=stub_runtime).execute(
        "read the note", {"tools": [{"name": "vault_read"}]}, session="s"
    )

    rec = recorder.load()[-1]
    assert rec["actor_supplied"] is True
    assert rec["kernel_state"] == "REJECTED"
    assert "unknown actor" in rec["reason"]
