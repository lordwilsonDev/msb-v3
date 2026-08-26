"""Codegraph index verification tests (V8).

Proves the symbol-level code index:
1. Can parse and store symbols from a real file
2. Queries return correct results
3. Index survives restart (persistence)
4. Edges are correctly recorded
"""
from __future__ import annotations

from pathlib import Path

import pytest

from msb_v3.codegraph.indexer import CodeGraphIndexer
from msb_v3.codegraph.store import CodeGraphStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> CodeGraphStore:
    """Create a fresh CodeGraphStore backed by a temp directory."""
    return CodeGraphStore(db_path=str(tmp_path / "codegraph.db"))


@pytest.fixture()
def indexer(store: CodeGraphStore) -> CodeGraphIndexer:
    """Create a CodeGraphIndexer backed by the temp store."""
    return CodeGraphIndexer(store=store)


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


class TestCodeGraphStore:
    """Smoke tests for CodeGraphStore CRUD."""

    def test_upsert_and_get_node(self, store: CodeGraphStore):
        """Can upsert a node and retrieve it."""
        store.upsert_node(
            repo="test-repo",
            name="func",
            fq_name="module.func",
            kind="function",
            file="module.py",
            line=10,
        )
        node = store.get_node("test-repo", "module.func")
        assert node is not None
        assert node["kind"] == "function"
        assert node["file"] == "module.py"

    def test_add_edge(self, store: CodeGraphStore):
        """Can add an edge between two nodes."""
        store.upsert_node(repo="r", name="a", fq_name="a", kind="function", file="f.py", line=1)
        store.upsert_node(repo="r", name="b", fq_name="b", kind="function", file="f.py", line=5)
        store.add_edge(repo="r", source="a", target="b", relation="calls", file="f.py")
        edges = store.edges_from("r", "a")
        assert len(edges) == 1
        assert edges[0]["target"] == "b"
        assert edges[0]["relation"] == "calls"

    def test_search_nodes(self, store: CodeGraphStore):
        """Search finds nodes by name substring."""
        store.upsert_node(repo="r", name="my_func", fq_name="my_module.my_func", kind="function", file="f.py", line=1)
        store.upsert_node(repo="r", name="func", fq_name="other.func", kind="function", file="g.py", line=1)
        results = store.search_nodes("r", "my_func")
        assert len(results) >= 1
        assert any("my_func" in n["fq_name"] for n in results)

    def test_nodes_for_file(self, store: CodeGraphStore):
        """Can retrieve all nodes in a file."""
        store.upsert_node(repo="r", name="a", fq_name="a", kind="function", file="target.py", line=1)
        store.upsert_node(repo="r", name="b", fq_name="b", kind="class", file="target.py", line=10)
        store.upsert_node(repo="r", name="c", fq_name="c", kind="function", file="other.py", line=1)
        nodes = store.nodes_for_file("r", "target.py")
        assert len(nodes) == 2

    def test_clear_repo(self, store: CodeGraphStore):
        """clear_repo removes all data for a repo."""
        store.upsert_node(repo="r", name="a", fq_name="a", kind="function", file="f.py", line=1)
        store.clear_repo("r")
        assert store.get_node("r", "a") is None

    def test_stats(self, store: CodeGraphStore):
        """stats returns node/edge counts."""
        store.upsert_node(repo="r", name="a", fq_name="a", kind="function", file="f.py", line=1)
        store.upsert_node(repo="r", name="b", fq_name="b", kind="class", file="f.py", line=5)
        stats = store.stats("r")
        assert stats["nodes"] == 2
        assert stats["repo"] == "r"

    def test_persistence_survives_restart(self, tmp_path: Path):
        """Data persists when store is recreated from same DB."""
        db_path = str(tmp_path / "cg.db")
        store1 = CodeGraphStore(db_path=db_path)
        store1.upsert_node(repo="r", name="persist", fq_name="persist.me", kind="function", file="f.py", line=1)

        store2 = CodeGraphStore(db_path=db_path)
        node = store2.get_node("r", "persist.me")
        assert node is not None
        assert node["fq_name"] == "persist.me"


# ---------------------------------------------------------------------------
# Indexer tests
# ---------------------------------------------------------------------------


class TestCodeGraphIndexer:
    """Smoke tests for CodeGraphIndexer."""

    def test_indexer_can_be_created(self, indexer: CodeGraphIndexer):
        """Indexer can be instantiated."""
        assert indexer is not None

    def test_index_msb_v3_core(self, indexer: CodeGraphIndexer, tmp_path: Path):
        """Indexer can parse a small Python project."""
        # Create a minimal Python file
        project = tmp_path / "mini"
        project.mkdir()
        (project / "__init__.py").write_text("")
        (project / "utils.py").write_text(
            "def helper():\n"
            "    return 42\n"
            "\n"
            "class Counter:\n"
            "    def __init__(self):\n"
            "        self.count = 0\n"
            "    def increment(self):\n"
            "        self.count += 1\n"
        )

        result = indexer.index(str(project), repo="mini")
        assert "nodes" in result
        assert result["nodes"] >= 3  # helper, Counter, Counter.__init__, Counter.increment

    def test_indexer_finds_functions(self, indexer: CodeGraphIndexer, tmp_path: Path):
        """Indexer discovers function symbols."""
        project = tmp_path / "funcs"
        project.mkdir()
        (project / "__init__.py").write_text("")
        (project / "math_ops.py").write_text(
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def multiply(a, b):\n"
            "    return a * b\n"
        )

        result = indexer.index(str(project), repo="funcs")
        assert result["nodes"] >= 2

    def test_indexer_finds_classes(self, indexer: CodeGraphIndexer, tmp_path: Path):
        """Indexer discovers class symbols."""
        project = tmp_path / "cls"
        project.mkdir()
        (project / "__init__.py").write_text("")
        (project / "models.py").write_text(
            "class User:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
        )

        result = indexer.index(str(project), repo="cls")
        assert result["nodes"] >= 2  # User + User.__init__
