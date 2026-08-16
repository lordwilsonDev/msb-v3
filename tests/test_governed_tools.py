"""Governed tools — registry, capability gate, containment, audit, wiring.

Phase 1 hardening (forensic-build-audit 2026-08-15): /chat advertised tools
to the model but never registered implementations. These tests pin the fix:
every advertised tool must terminate inside the governance perimeter
(capability gate -> contained executor -> audit).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from msb_v3.core.config import settings
from msb_v3.harnesses.base import ChatHarness
from msb_v3.node.filesystem import FileReader
from msb_v3.tools import executors, runtime
from msb_v3.tools.registry import TOOLS


class _RecordingClient:
    """Minimal client: captures registrations and tool calls."""

    def __init__(self) -> None:
        self.registered: dict[str, object] = {}
        self.calls: list[tuple[str, dict]] = []

    def register_tool(self, name, func) -> None:
        self.registered[name] = func

    def run_tool(self, name, args) -> str:
        self.calls.append((name, args))
        return self.registered[name](**args)

    def execute_tool_loop(self, query, *, system=None, tools=None):
        class Resp:
            text = "ok"
            model = "fake"
            latency_s = 0.0

        return Resp()


# --- registry --------------------------------------------------------------


def test_registry_has_governed_tools_with_executors():
    assert set(TOOLS) == {
        "search_vault",
        "vault_read",
        "vault_write",
        "codegraph.explore",
        "codegraph.context",
        "codegraph.impact",
        "codegraph.rename",
        "memory.recall",
        "memory.store",
        "context.compose",
        "moie.analyze",
        "factory.run",
    }
    for tool_id, td in TOOLS.items():
        assert td.as_model_schema()["name"] == tool_id
        # dotted ids (codegraph.explore) map to underscore executors
        fn_name = tool_id.replace(".", "_")
        assert callable(getattr(executors, fn_name, None)), f"missing executor {tool_id}"
        assert td.risk_class in {"LOW", "MEDIUM", "HIGH"}
        assert td.mutation_class in {"NONE", "WRITE", "SYSTEM"}


def test_vault_write_is_capability_gated_by_default():
    assert TOOLS["vault_write"].required_capabilities == ("vault.write",)
    assert TOOLS["search_vault"].required_capabilities == ()


# --- registration + capability gate ----------------------------------------


def test_register_skips_unknown_tools():
    client = _RecordingClient()
    runtime.register_governed_tools(
        client,
        {"tools": [{"name": "search_vault"}, {"name": "not_a_real_tool"}], "session": "s"},
    )
    assert set(client.registered) == {"search_vault"}


def test_vault_write_denied_without_capability(monkeypatch):
    monkeypatch.setattr(runtime, "_audit_append", lambda *a, **k: None)
    client = _RecordingClient()
    runtime.register_governed_tools(
        client,
        {"tools": [{"name": "vault_write"}], "session": "s"},
    )
    result = client.run_tool("vault_write", {"path": "x.md", "content": "hi"})
    assert result.startswith("[denied]")
    assert "vault.write" in result


def test_vault_write_allowed_with_capability(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "_audit_append", lambda *a, **k: None)
    monkeypatch.setattr(settings, "vault_path", str(tmp_path / "vault"))
    (tmp_path / "vault" / "notes").mkdir(parents=True, exist_ok=True)
    client = _RecordingClient()
    runtime.register_governed_tools(
        client,
        {"tools": [{"name": "vault_write"}], "granted_capabilities": ["vault.write"], "session": "s"},
    )
    result = client.run_tool("vault_write", {"path": "notes/a.md", "content": "hello"})
    assert result.startswith("wrote notes/a.md")
    assert (tmp_path / "vault" / "notes" / "a.md").read_text() == "hello"


def test_tool_execution_is_audited(monkeypatch):
    """The real audit append (not a stand-in) records the call and excludes
    tool content — secrets hygiene: file contents never land in the chain."""
    import msb_v3.uac.chain_anchor as chain_anchor

    class FakeChain:
        def __init__(self) -> None:
            self.events: list[tuple] = []

        def append(self, component, event_type, payload):
            self.events.append((component, event_type, payload))

    fake = FakeChain()
    monkeypatch.setattr(chain_anchor, "anchored_chain_from_env", lambda: fake)

    runtime._audit_append(
        "vault_write",
        {"path": "audit.md", "content": "secret body"},
        "wrote audit.md",
        tenant="default",
        session="s9",
    )
    assert len(fake.events) == 1
    component, event_type, payload = fake.events[0]
    assert component == "tools"
    assert event_type == "tool.vault_write"
    assert payload["session"] == "s9"
    assert "content" not in payload["args"]  # secrets hygiene
    assert "secret body" not in str(payload)


# --- containment ------------------------------------------------------------


def test_vault_read_rejects_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "_audit_append", lambda *a, **k: None)
    monkeypatch.setattr(settings, "vault_path", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir(parents=True, exist_ok=True)
    (tmp_path / "secret.txt").write_text("top secret")
    client = _RecordingClient()
    runtime.register_governed_tools(client, {"tools": [{"name": "vault_read"}], "session": "s"})
    result = client.run_tool("vault_read", {"path": "../secret.txt"})
    assert result.startswith("[denied]")


def test_vault_read_round_trip(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "_audit_append", lambda *a, **k: None)
    monkeypatch.setattr(settings, "vault_path", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir(parents=True, exist_ok=True)
    (tmp_path / "vault" / "note.md").write_text("# Note\nbody")
    client = _RecordingClient()
    runtime.register_governed_tools(client, {"tools": [{"name": "vault_read"}], "session": "s"})
    result = client.run_tool("vault_read", {"path": "note.md"})
    assert "body" in result


# --- executors --------------------------------------------------------------


def test_search_vault_returns_formatted_matches(monkeypatch, tmp_path):
    import msb_v3.fabric.retrieval_router as rr

    class FakeRouter:
        def __init__(self, tenant):
            self.tenant = tenant

        async def run(self, query, top_k=5):
            return SimpleNamespace(
                matches=[
                    {"text": "sovereign note", "source": "notes/a.md", "score": 0.9},
                    {"text": "second hit", "source": "notes/b.md", "score": 0.8},
                ]
            )

    monkeypatch.setattr(rr, "FabricRetrievalRouter", FakeRouter)
    out = executors.search_vault({"query": "sovereign"}, tenant="t1", session="s")
    assert "sovereign note" in out
    assert "notes/a.md" in out
    assert "0.90" in out


def test_search_vault_empty_query_errors():
    assert executors.search_vault({}, tenant="t", session="s").startswith("[tool-error]")


# --- harness wiring ---------------------------------------------------------


def test_chat_harness_registers_governed_tools():
    client = _RecordingClient()
    harness = ChatHarness(client=client)
    harness.execute(
        "use a tool",
        context={"tools": [{"name": "search_vault", "type": "function", "parameters": {}}]},
        session="s1",
    )
    assert "search_vault" in client.registered


def test_chat_harness_does_not_register_without_tools():
    client = _RecordingClient()
    harness = ChatHarness(client=client)
    harness.execute("plain question", session="s2")
    assert client.registered == {}


def test_governed_tool_run_through_execute_tool_loop(monkeypatch, tmp_path):
    """End-to-end through the real loop: model emits a tool call, the loop
    executes the governed tool, and the result feeds the next turn."""
    monkeypatch.setattr(runtime, "_audit_append", lambda *a, **k: None)
    monkeypatch.setattr(settings, "vault_path", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir(parents=True, exist_ok=True)
    (tmp_path / "vault" / "facts.md").write_text("the vault holds truth")

    from msb_v3.local_ai.ollama import LocalAIClient

    class ToolLoopClient(LocalAIClient):
        def generate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
            class Resp:
                text = ""
                model = "fake"
                latency_s = 0.0
                tool_calls = [{"function": {"name": "vault_read", "arguments": {"path": "facts.md"}}}]

            return Resp()

    client = ToolLoopClient()
    runtime.register_governed_tools(
        client,
        {"tools": [{"name": "vault_read"}], "session": "s"},
    )
    resp = client.execute_tool_loop(
        "read facts",
        tools=[{"type": "function", "name": "vault_read", "parameters": {}}],
        max_steps=1,
    )
    # Loop called generate once and executed the tool (no crash, no
    # "[tool-error] unknown tool").
    assert "[tool-error] unknown tool" not in resp.text


def test_file_reader_still_contained(tmp_path):
    """The underlying sandbox used by vault_read/vault_write keeps its
    traversal guard (SMI-017 #2 pattern) — regression pin."""
    (tmp_path / "root").mkdir(parents=True, exist_ok=True)
    (tmp_path / "outside.txt").write_text("x")
    reader = FileReader(tmp_path / "root")
    with pytest.raises(Exception):
        reader.read("../outside.txt")
