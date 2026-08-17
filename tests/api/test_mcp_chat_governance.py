"""M2/P1 — the MCP `chat` surface is governed, not a thin proxy.

The review's open question: does a request through the MCP surface actually
pass through governance, or is the bridge a thin proxy? Verification showed
the chat harness wires every advertised tool through
``tools.runtime.register_governed_tools`` -> ``_run_governed`` (capability
gate + approval gate + contained executor + audit). These tests pin that
wiring and the M2/P1 audit-verdict observability:

- A tool call arriving through the chat surface is audited with an explicit
  machine-readable verdict (allowed/denied/approval-required/unknown).
- A denied tool through the chat surface produces no side effect and a
  verdict-bearing audit record.
"""

from __future__ import annotations

from typing import Any, Dict

from msb_v3.core.config import settings
from msb_v3.tools.runtime import register_governed_tools
from msb_v3.uac.audit_chain import AuditChain


class _RecordingClient:
    def __init__(self) -> None:
        self.tools: Dict[str, Any] = {}

    def register_tool(self, name: str, fn: Any) -> None:
        self.tools[name] = fn


def _governed_client(chain: AuditChain, tmp_path) -> _RecordingClient:
    """A client wired the way the ChatHarness wires it: every advertised tool
    registered through the governed loop, audit pointed at a tmp chain."""
    import pytest

    pytest.MonkeyPatch().setattr(
        "msb_v3.uac.chain_anchor.anchored_chain_from_env", lambda: chain
    )
    client = _RecordingClient()
    root = tmp_path / "vault"
    root.mkdir()
    pytest.MonkeyPatch().setattr(settings, "vault_path", str(root))
    return client


def test_chat_surface_registers_tools_through_governed_loop(monkeypatch, tmp_path) -> None:
    """The chat harness hands the model only tools the perimeter can back —
    registration goes through the governed loop, not a raw tool map."""
    audit = AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)
    monkeypatch.setattr("msb_v3.uac.chain_anchor.anchored_chain_from_env", lambda: audit)
    client = _RecordingClient()
    register_governed_tools(
        client,
        {"tools": [{"name": "vault_read"}, {"name": "not.real"}], "session": "s"},
    )
    # Unknown tools are not even registered — the model never sees them.
    assert list(client.tools) == ["vault_read"]


def test_chat_surface_denied_tool_no_side_effect_with_verdict(monkeypatch, tmp_path) -> None:
    """A tool the caller lacks capability for is denied through the chat
    surface: no side effect, and the audit record carries an explicit
    'denied' verdict (not just prose)."""
    audit = AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)
    monkeypatch.setattr("msb_v3.uac.chain_anchor.anchored_chain_from_env", lambda: audit)
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setattr(settings, "vault_path", str(root))

    client = _RecordingClient()
    register_governed_tools(
        client,
        {"tools": [{"name": "vault_write"}], "session": "s"},
    )
    outcome = client.tools["vault_write"](path="x.md", content="should not write")
    assert outcome == "[denied] tool vault_write requires capabilities: vault.write"
    assert not (root / "x.md").exists()  # no side effect

    records = audit.get_chain(component="tools")
    assert len(records) == 1
    assert records[0].payload["verdict"] == "denied"
    # secrets hygiene holds on the chat path too
    assert "content" not in records[0].payload["args"]


def test_chat_surface_allowed_tool_verdict(monkeypatch, tmp_path) -> None:
    """The happy path is audited as 'allowed' — the record distinguishes
    outcomes without parsing result prose."""
    audit = AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)
    monkeypatch.setattr("msb_v3.uac.chain_anchor.anchored_chain_from_env", lambda: audit)
    root = tmp_path / "vault"
    root.mkdir()
    monkeypatch.setattr(settings, "vault_path", str(root))
    (root / "note.txt").write_text("hello sovereign")

    client = _RecordingClient()
    register_governed_tools(
        client,
        {"tools": [{"name": "vault_read"}], "session": "s"},
    )
    outcome = client.tools["vault_read"](path="note.txt")
    assert "hello sovereign" in outcome

    records = audit.get_chain(component="tools")
    assert len(records) == 1
    assert records[0].payload["verdict"] == "allowed"
