"""Phase 4 — governed tool loop: prove the five cases hermetic.

The tool loop is capability → policy → execution → result → audit, run through
``tools/runtime._run_governed`` (approval gate + capability gate + contained
executor + audit) with registration via ``register_governed_tools``. These tests
prove, without a model or Qdrant:

    A permitted operation      -> execute + audit
    B unauthorized operation   -> deny + no execution + audit denial
    C approval-required        -> refuse unless pre-approved, then proceed
    D kill switch              -> BLOCK before any execution
    E malformed / unknown      -> reject + no execution + evidence
"""

from __future__ import annotations

import pytest

from msb_v3.core.config import settings
from msb_v3.tools.registry import MUTATION_WRITE, RISK_HIGH, TOOLS, ToolDef
from msb_v3.tools.runtime import _run_governed, register_governed_tools
from msb_v3.uac.audit_chain import AuditChain


@pytest.fixture()
def chain(monkeypatch, tmp_path):
    """Point the tools' audit appends at a tmp chain (they import
    ``anchored_chain_from_env`` fresh inside ``_audit_append``)."""
    audit = AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)
    monkeypatch.setattr("msb_v3.uac.chain_anchor.anchored_chain_from_env", lambda: audit)
    return audit


@pytest.fixture()
def vault(monkeypatch, tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setattr(settings, "vault_path", str(root))
    return root


def _recorded(chain: AuditChain) -> list[str]:
    return [r.event_type for r in chain.get_chain(component="tools")]


def _verdicts(chain: AuditChain) -> list[str]:
    return [r.payload.get("verdict", "<missing>") for r in chain.get_chain(component="tools")]


class _RecordingClient:
    def __init__(self) -> None:
        self.tools: dict = {}

    def register_tool(self, name: str, fn) -> None:
        self.tools[name] = fn


# --- A. permitted operation ------------------------------------------------


def test_permitted_vault_read_executes_and_audits(chain, vault):
    (vault / "note.txt").write_text("hello sovereign")
    result = _run_governed("vault_read", {"path": "note.txt"}, granted=frozenset(), tenant="t", session="s")
    assert "hello sovereign" in result
    assert _recorded(chain) == ["tool.vault_read"]
    assert _verdicts(chain) == ["allowed"]


def test_permitted_memory_store_executes_with_capability(chain, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "memory_fabric_db_path", str(tmp_path / "fabric.db"))
    result = _run_governed(
        "memory.store",
        {"content": "decide: use SQLite", "type": "semantic"},
        granted=frozenset({"memory.write"}),
        tenant="t",
        session="s",
    )
    assert result.startswith("stored ")
    assert _recorded(chain) == ["tool.memory.store"]


# --- B. unauthorized operation ---------------------------------------------


def test_unauthorized_vault_write_is_denied_without_execution(chain, vault):
    result = _run_governed(
        "vault_write",
        {"path": "x.md", "content": "should not write"},
        granted=frozenset(),
        tenant="t",
        session="s",
    )
    assert result == "[denied] tool vault_write requires capabilities: vault.write"
    assert not (vault / "x.md").exists()  # no execution
    # audit denial: the refusal left a record with an explicit verdict
    assert _recorded(chain) == ["tool.vault_write"]
    assert _verdicts(chain) == ["denied"]
    assert "denied" in chain.get_chain(component="tools")[0].payload["result_head"]
    # secrets hygiene: content is excluded from the audit payload
    assert "content" not in chain.get_chain(component="tools")[0].payload["args"]


def test_unauthorized_memory_store_is_denied(chain, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "memory_fabric_db_path", str(tmp_path / "fabric.db"))
    result = _run_governed(
        "memory.store",
        {"content": "secret"},
        granted=frozenset(),
        tenant="t",
        session="s",
    )
    assert result == "[denied] tool memory.store requires capabilities: memory.write"


# --- C. approval-required ---------------------------------------------------


@pytest.fixture()
def approval_demo(monkeypatch):
    monkeypatch.setitem(
        TOOLS,
        "approval.demo",
        ToolDef(
            tool_id="approval.demo",
            description="approval-required demo",
            parameters={},
            risk_class=RISK_HIGH,
            mutation_class=MUTATION_WRITE,
            required_capabilities=(),
            approval_required=True,
        ),
    )


def test_approval_required_refuses_then_proceeds_when_approved(chain, approval_demo):
    refused = _run_governed("approval.demo", {}, granted=frozenset(), tenant="t", session="s")
    assert refused == "[approval-required] tool approval.demo requires operator approval"
    # the refusal left evidence too, with an explicit verdict
    assert _recorded(chain) == ["tool.approval.demo"]
    assert _verdicts(chain) == ["approval-required"]

    proceeded = _run_governed(
        "approval.demo",
        {},
        granted=frozenset(),
        tenant="t",
        session="s",
        approved=frozenset({"approval.demo"}),
    )
    # gate passed -> falls through to the (absent) executor lookup
    assert proceeded == "[tool-error] no executor registered for approval.demo"


def test_registration_forwards_approved_tools(chain, approval_demo):
    client = _RecordingClient()
    register_governed_tools(
        client,
        {"tools": [{"name": "approval.demo"}], "approved_tools": ["approval.demo"], "session": "s"},
    )
    assert client.tools["approval.demo"]() == "[tool-error] no executor registered for approval.demo"


# --- D. kill switch ----------------------------------------------------------


class _Switch:
    def __init__(self, armed: bool) -> None:
        self._armed = armed

    def is_armed(self) -> bool:
        return self._armed


class _Audit:
    def append(self, component, event_type, payload):
        return None


def test_kill_switch_blocks_tool_capability_and_clear_gate_allows(chain):
    from msb_v3.agent.safety import ActionGate

    blocked = ActionGate(killswitch=_Switch(armed=True), audit_chain=_Audit()).gate(
        "write_file", approved={"write_file"}
    )
    assert blocked.action == "BLOCK"
    assert "kill switch" in blocked.reason

    clear = ActionGate(killswitch=_Switch(armed=False), audit_chain=_Audit()).gate(
        "write_file", approved={"write_file"}
    )
    assert clear.allowed is True
    assert clear.action == "SAFE"


# --- E. malformed / unknown -------------------------------------------------


def test_malformed_and_unknown_tool_requests_are_rejected_with_evidence(chain, vault):
    # missing required argument -> structured error, no execution
    missing = _run_governed("vault_read", {}, granted=frozenset(), tenant="t", session="s")
    assert missing.startswith("[tool-error] vault_read: path is required")

    # unknown tool -> rejected with evidence (verdict "unknown")
    unknown = _run_governed("no.such.tool", {}, granted=frozenset(), tenant="t", session="s")
    assert unknown == "[tool-error] unknown tool: no.such.tool"

    assert _recorded(chain) == ["tool.vault_read", "tool.no.such.tool"]
    # The malformed call was ALLOWED by the gate (capability held) and the
    # executor rejected the bad args — so the gate verdict is "allowed" and
    # the execution error lives in result_head. The unknown tool never got a
    # gate decision at all: verdict "unknown".
    assert _verdicts(chain) == ["allowed", "unknown"]
    assert "path is required" in chain.get_chain(component="tools")[0].payload["result_head"]


def test_registration_skips_unknown_tools():
    client = _RecordingClient()
    register_governed_tools(client, {"tools": [{"name": "vault_read"}, {"name": "not.real"}]})
    assert list(client.tools) == ["vault_read"]
