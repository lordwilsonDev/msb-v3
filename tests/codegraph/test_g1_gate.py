"""Validation gate G1 (spec §7): symbol queries answer in <1s.

Indexes msb-v3's own source (~11k LOC) and times the callers query —
the exact query an agent asks before editing a function. The graph is
keyed per repo in a temp DB, so this never touches production data.
"""

import time
from pathlib import Path

import pytest

from msb_v3.codegraph.indexer import CodeGraphIndexer
from msb_v3.codegraph.queries import CodeGraphQueries
from msb_v3.codegraph.store import CodeGraphStore

REPO = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture(scope="module")
def live_graph(tmp_path_factory):
    store = CodeGraphStore(str(tmp_path_factory.mktemp("cg") / "graph.db"))
    indexer = CodeGraphIndexer(store)
    result = indexer.index(str(REPO))
    assert result["ok"]
    return store, CodeGraphQueries(store), result


def test_index_builds_over_msb_source(live_graph):
    _, _, result = live_graph
    assert result["nodes"] > 500  # msb-v3 has hundreds of symbols
    assert result["edges"] > 500
    # every source file parsed; nothing silently skipped as a parse error
    assert result["parse_errors"] == []


def test_callers_query_under_one_second(live_graph):
    _, queries, _ = live_graph
    # callers_of on a genuinely-called local symbol: create_app is invoked
    # by the server entry point (and tests).
    t0 = time.perf_counter()
    callers = queries.callers_of(str(REPO), "create_app")
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"G1 gate failed: callers query took {elapsed:.3f}s"
    assert callers  # the server entry point calls create_app


def test_references_query_under_one_second(live_graph):
    _, queries, _ = live_graph
    # require_operator is passed to Depends() — references, not calls. The
    # graph must answer both within the gate.
    t0 = time.perf_counter()
    refs = queries.references_of(str(REPO), "require_operator")
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"G1 gate failed: references query took {elapsed:.3f}s"
    assert refs


def test_context_query_under_one_second(live_graph):
    _, queries, _ = live_graph
    t0 = time.perf_counter()
    ctx = queries.context_of(str(REPO), "require_operator")
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0
    assert ctx["found"]


def test_impact_query_under_one_second(live_graph):
    _, queries, _ = live_graph
    t0 = time.perf_counter()
    report = queries.impact_of(str(REPO), "msb_v3/api/auth.py")
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0
    assert report["seeds"]
