"""Governed tool wiring — the four codegraph tools are registered and
their execution path terminates inside the perimeter (registry -> executor
-> local SQLite graph)."""

from pathlib import Path

import pytest

from msb_v3.tools import executors
from msb_v3.tools.registry import TOOLS

# Index the fixtures PARENT so rel paths include sample_repo/.
FIXTURES = Path(__file__).parent / "fixtures"
REPO = str(FIXTURES)


@pytest.fixture()
def indexed(tmp_path, monkeypatch):
    from msb_v3.codegraph.indexer import CodeGraphIndexer
    from msb_v3.codegraph.store import CodeGraphStore
    from msb_v3.core.config import settings

    monkeypatch.setattr(settings, "codegraph_db_path", str(tmp_path / "graph.db"))
    CodeGraphIndexer(CodeGraphStore(settings.codegraph_db_path)).index(REPO)
    return settings.codegraph_db_path


def test_registry_has_four_codegraph_tools():
    ids = {t: d for t, d in TOOLS.items() if t.startswith("codegraph.")}
    assert set(ids) == {"codegraph.explore", "codegraph.context", "codegraph.impact", "codegraph.rename"}
    for d in ids.values():
        assert d.risk_class == "LOW"
        assert d.mutation_class == "NONE"
        assert d.required_capabilities == ()  # read-only: no extra grant needed


def test_registry_tools_have_executors():
    for tool_id in TOOLS:
        if tool_id.startswith("codegraph."):
            # dotted ids map to underscore executors (runtime convention)
            fn_name = tool_id.replace(".", "_")
            assert callable(getattr(executors, fn_name, None)), f"{tool_id} has no executor"


def test_explore_executor(indexed):
    out = executors.codegraph_explore({"repo": REPO, "name": "Engine"}, tenant="t", session="s")
    assert "[class]" in out and "Engine" in out


def test_context_executor(indexed):
    out = executors.codegraph_context(
        {"repo": REPO, "symbol": "sample_repo.engine.compute"}, tenant="t", session="s"
    )
    assert "callers:" in out and "callees:" in out


def test_impact_executor(indexed):
    out = executors.codegraph_impact(
        {"repo": REPO, "file": "sample_repo/engine.py"}, tenant="t", session="s"
    )
    assert "dependents" in out


def test_rename_executor(indexed):
    out = executors.codegraph_rename({"repo": REPO, "name": "compute"}, tenant="t", session="s")
    assert "reference(s)" in out


def test_unindexed_repo_is_honest(indexed):
    out = executors.codegraph_explore({"repo": "/does/not/exist", "name": "x"}, tenant="t", session="s")
    assert "indexed" in out or "No symbols" in out  # never a fake empty success


def test_missing_args_return_tool_error(indexed):
    out = executors.codegraph_explore({"repo": REPO}, tenant="t", session="s")
    assert out.startswith("[tool-error]")


def test_runtime_gate_audits_codegraph_call(tmp_path, monkeypatch):
    """Full governed path: register -> capability gate -> executor -> audit."""
    from msb_v3.core.config import settings
    from msb_v3.tools.registry import model_schemas
    from msb_v3.tools.runtime import register_governed_tools

    monkeypatch.setattr(settings, "codegraph_db_path", str(tmp_path / "graph.db"))
    from msb_v3.codegraph.indexer import CodeGraphIndexer
    from msb_v3.codegraph.store import CodeGraphStore

    CodeGraphIndexer(CodeGraphStore(settings.codegraph_db_path)).index(REPO)

    calls: dict = {}

    class FakeClient:
        def register_tool(self, name, fn):
            calls[name] = fn

    schemas = model_schemas(["codegraph.explore", "codegraph.context"])
    register_governed_tools(
        FakeClient(), {"tools": schemas, "granted_capabilities": [], "tenant": "t", "session": "s"}
    )
    assert set(calls) == {"codegraph.explore", "codegraph.context"}
    out = calls["codegraph.explore"](repo=REPO, name="Engine")
    assert "[class]" in out
