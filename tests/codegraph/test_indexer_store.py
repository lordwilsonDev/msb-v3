"""Indexer + store tests — fixture repo -> SQLite graph, then assertions."""

from pathlib import Path

import pytest

from msb_v3.codegraph.indexer import CodeGraphIndexer
from msb_v3.codegraph.queries import CodeGraphQueries
from msb_v3.codegraph.store import CodeGraphStore

# Index the fixtures PARENT so rel paths include sample_repo/ — the same
# shape as a real checkout (the G1 test indexes above msb_v3/ the same way).
FIXTURES = Path(__file__).parent / "fixtures"
REPO = str(FIXTURES)


@pytest.fixture()
def graph(tmp_path):
    store = CodeGraphStore(str(tmp_path / "graph.db"))
    indexer = CodeGraphIndexer(store)
    result = indexer.index(REPO)
    assert result["ok"]
    return store, CodeGraphQueries(store), result, REPO


def test_index_builds_graph(graph):
    _, _, result, _ = graph
    assert result["nodes"] > 0
    assert result["edges"] > 0
    stats = result["stats"]
    assert stats["nodes_by_kind"]["function"] >= 2
    assert stats["nodes_by_kind"]["class"] >= 1
    assert stats["nodes_by_kind"]["method"] >= 3


def test_find_symbol(graph):
    store, queries, _, repo = graph
    hits = queries.find_symbol(repo, "Engine")
    assert hits and hits[0]["kind"] == "class"
    hits = queries.find_symbol(repo, "compute")
    assert any(h["name"] == "compute" for h in hits)


def test_callers_of(graph):
    store, queries, _, repo = graph
    # main.py calls compute() directly
    callers = queries.callers_of(repo, "sample_repo.engine.compute")
    assert callers
    sources = {c["source"] for c in callers}
    assert "sample_repo.main.main" in sources
    assert "sample_repo.engine.Engine.run" in sources


def test_callees_of(graph):
    store, queries, _, repo = graph
    callees = queries.callees_of(repo, "sample_repo.engine.compute")
    targets = {c["target"] for c in callees}
    assert "sample_repo.utils.normalize" in targets


def test_impact_of_file(graph):
    store, queries, _, repo = graph
    report = queries.impact_of(repo, "sample_repo/engine.py")
    assert report["seeds"]
    deps = {d["symbol"] for d in report["dependents"]}
    # main.main calls engine symbols -> transitively in the blast radius
    assert "sample_repo.main.main" in deps


def test_context_of(graph):
    store, queries, _, repo = graph
    ctx = queries.context_of(repo, "sample_repo.engine.compute")
    assert ctx["found"]
    assert ctx["kind"] == "function"
    caller_syms = {c["symbol"] for c in ctx["callers"]}
    assert "sample_repo.main.main" in caller_syms
    callee_syms = {c["symbol"] for c in ctx["callees"]}
    assert "sample_repo.utils.normalize" in callee_syms


def test_context_unknown_returns_candidates(graph):
    store, queries, _, repo = graph
    ctx = queries.context_of(repo, "does_not_exist")
    assert not ctx["found"]
    assert "candidates" in ctx


def test_rename_preview(graph):
    store, queries, _, repo = graph
    preview = queries.rename_preview(repo, "compute")
    assert preview["definitions"]
    assert preview["reference_count"] > 0
    assert any(r["relation"] == "calls" for r in preview["references"])


def test_impact_line_scoped(graph):
    store, queries, _, repo = graph
    # Engine class definition line
    report = queries.impact_of(repo, "sample_repo/engine.py", line=1)
    assert report["line"] == 1
    # line 1 is the docstring -> seeds fall back to the file's symbols
    assert report["seeds"]
