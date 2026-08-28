"""META-2: ImportGraph tests — graph-based context selection.

Tests verify:
  - Graph construction from adjacency dict
  - Direct deps and reverse deps
  - Transitive deps (BFS forward)
  - Transitive importers (BFS reverse)
  - Distance computation
  - Hub file detection
  - Impact zone computation
  - Cluster extraction
  - Relevance scoring with graph distance
  - Empty graph behavior
  - Self-loop handling
  - Max depth limiting
  - GraphStats
"""

from __future__ import annotations

from msb_v3.meta.translation.import_graph import ImportGraph

# -- Test fixtures -----------------------------------------------------------

# Linear: auth → crypto → config
LINEAR = {
    "src/auth.py": ["src/crypto.py"],
    "src/crypto.py": ["src/config.py"],
    "src/config.py": [],
}

# Diamond: project → auth, project → db; auth → config, db → config
DIAMOND = {
    "src/project.py": ["src/auth.py", "src/db.py"],
    "src/auth.py": ["src/config.py"],
    "src/db.py": ["src/config.py"],
    "src/config.py": [],
}

# Hub: everything imports core
HUB = {
    "src/core.py": [],
    "src/auth.py": ["src/core.py"],
    "src/db.py": ["src/core.py"],
    "src/api.py": ["src/core.py", "src/auth.py"],
    "src/utils.py": ["src/core.py"],
}

# Isolated: one disconnected node
ISOLATED = {
    "src/a.py": ["src/b.py"],
    "src/b.py": [],
    "src/orphan.py": [],
}


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestImportGraphConstruction:
    def test_from_adjacency(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        assert len(g.nodes()) == 3

    def test_empty_graph(self) -> None:
        g = ImportGraph.empty()
        assert len(g.nodes()) == 0
        assert g.direct_deps("anything") == set()

    def test_nodes_include_reverse_only_targets(self) -> None:
        g = ImportGraph.from_adjacency({"a.py": ["b.py"]})
        assert "a.py" in g.nodes()
        assert "b.py" in g.nodes()


# ---------------------------------------------------------------------------
# Direct deps
# ---------------------------------------------------------------------------

class TestDirectDeps:
    def test_linear_chain(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        assert g.direct_deps("src/auth.py") == {"src/crypto.py"}
        assert g.direct_deps("src/crypto.py") == {"src/config.py"}
        assert g.direct_deps("src/config.py") == set()

    def test_unknown_file_returns_empty(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        assert g.direct_deps("nonexistent.py") == set()

    def test_direct_importers(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        assert g.direct_importers("src/config.py") == {"src/crypto.py"}
        assert g.direct_importers("src/crypto.py") == {"src/auth.py"}
        assert g.direct_importers("src/auth.py") == set()


# ---------------------------------------------------------------------------
# Transitive deps (forward BFS)
# ---------------------------------------------------------------------------

class TestTransitiveDeps:
    def test_linear_chain(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        assert g.transitive_deps("src/auth.py") == {"src/crypto.py", "src/config.py"}

    def test_leaf_has_no_deps(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        assert g.transitive_deps("src/config.py") == set()

    def test_diamond(self) -> None:
        g = ImportGraph.from_adjacency(DIAMOND)
        deps = g.transitive_deps("src/project.py")
        assert deps == {"src/auth.py", "src/db.py", "src/config.py"}

    def test_max_depth_limits_expansion(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        # Depth 1: auth → crypto
        deps = g.transitive_deps("src/auth.py", max_depth=1)
        assert deps == {"src/crypto.py"}
        assert "src/config.py" not in deps  # too deep

    def test_self_loop_ignored(self) -> None:
        g = ImportGraph.from_adjacency({"a.py": ["a.py", "b.py"], "b.py": []})
        deps = g.transitive_deps("a.py")
        assert deps == {"b.py"}  # self not included


# ---------------------------------------------------------------------------
# Transitive importers (reverse BFS)
# ---------------------------------------------------------------------------

class TestTransitiveImporters:
    def test_linear_chain(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        importers = g.transitive_importers("src/config.py")
        assert importers == {"src/crypto.py", "src/auth.py"}

    def test_leaf_importer(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        importers = g.transitive_importers("src/auth.py")
        assert importers == set()  # nothing imports auth

    def test_hub_reverse(self) -> None:
        g = ImportGraph.from_adjacency(HUB)
        importers = g.transitive_importers("src/core.py")
        assert importers == {"src/auth.py", "src/db.py", "src/api.py", "src/utils.py"}


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------

class TestDistance:
    def test_self_distance_zero(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        assert g.distance("src/auth.py", "src/auth.py") == 0

    def test_direct_neighbor(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        assert g.distance("src/auth.py", "src/crypto.py") == 1

    def test_transitive_neighbor(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        assert g.distance("src/auth.py", "src/config.py") == 2

    def test_unreachable_returns_none(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        assert g.distance("src/config.py", "src/auth.py") is None

    def test_diamond_shortest_path(self) -> None:
        g = ImportGraph.from_adjacency(DIAMOND)
        # auth → config is 1 hop, not 2 via project
        assert g.distance("src/auth.py", "src/config.py") == 1

    def test_unknown_file_returns_none(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        assert g.distance("nonexistent.py", "src/auth.py") is None


# ---------------------------------------------------------------------------
# Hub detection
# ---------------------------------------------------------------------------

class TestHubDetection:
    def test_hub_file_is_most_connected(self) -> None:
        g = ImportGraph.from_adjacency(HUB)
        hubs = g.hub_files(top_n=3)
        assert hubs[0] == "src/core.py"  # imported by 4 files

    def test_linear_no_clear_hub(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        hubs = g.hub_files(top_n=3)
        assert len(hubs) == 3


# ---------------------------------------------------------------------------
# Impact zone
# ---------------------------------------------------------------------------

class TestImpactZone:
    def test_changing_config_breaks_everything(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        impact = g.impact_zone("src/config.py")
        assert impact == {"src/crypto.py", "src/auth.py"}

    def test_changing_leaf_has_no_impact(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        impact = g.impact_zone("src/auth.py")
        assert impact == set()

    def test_hub_change_impacts_all(self) -> None:
        g = ImportGraph.from_adjacency(HUB)
        impact = g.impact_zone("src/core.py")
        assert impact == {"src/auth.py", "src/db.py", "src/api.py", "src/utils.py"}


# ---------------------------------------------------------------------------
# Cluster
# ---------------------------------------------------------------------------

class TestCluster:
    def test_cluster_includes_seed(self) -> None:
        g = ImportGraph.from_adjacency(DIAMOND)
        cluster = g.cluster("src/auth.py", max_depth=1)
        assert "src/auth.py" in cluster
        assert "src/config.py" in cluster  # direct dep
        assert "src/project.py" in cluster  # direct importer

    def test_cluster_limits_depth(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        cluster = g.cluster("src/auth.py", max_depth=1)
        # auth → crypto (forward), nothing imports auth (backward)
        assert cluster == {"src/auth.py", "src/crypto.py"}


# ---------------------------------------------------------------------------
# Relevance scores
# ---------------------------------------------------------------------------

class TestRelevanceScores:
    def test_seed_gets_1point0(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        scores = g.relevance_scores(
            seed_files=["src/auth.py"],
            candidate_files=["src/auth.py"],
        )
        assert scores["src/auth.py"] == 1.0

    def test_direct_dep_scores_high(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        scores = g.relevance_scores(
            seed_files=["src/auth.py"],
            candidate_files=["src/crypto.py"],
        )
        assert scores["src/crypto.py"] >= 0.85

    def test_transitive_dep_scores_lower(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        scores = g.relevance_scores(
            seed_files=["src/auth.py"],
            candidate_files=["src/crypto.py", "src/config.py"],
        )
        assert scores["src/crypto.py"] > scores["src/config.py"]

    def test_unreachable_gets_zero(self) -> None:
        g = ImportGraph.from_adjacency(ISOLATED)
        scores = g.relevance_scores(
            seed_files=["src/a.py"],
            candidate_files=["src/orphan.py"],
        )
        assert scores["src/orphan.py"] == 0.0

    def test_reverse_dep_bonus(self) -> None:
        g = ImportGraph.from_adjacency(LINEAR)
        scores = g.relevance_scores(
            seed_files=["src/config.py"],  # config is the seed
            candidate_files=["src/crypto.py"],  # crypto imports config → reverse dep
        )
        # crypto is a reverse dep of config → gets bonus
        assert scores["src/crypto.py"] > 0.0

    def test_diamond_symmetry(self) -> None:
        g = ImportGraph.from_adjacency(DIAMOND)
        scores = g.relevance_scores(
            seed_files=["src/project.py"],
            candidate_files=["src/auth.py", "src/db.py"],
        )
        # Both are direct deps at distance 1 → same score
        assert abs(scores["src/auth.py"] - scores["src/db.py"]) < 0.01


# ---------------------------------------------------------------------------
# GraphStats
# ---------------------------------------------------------------------------

class TestGraphStats:
    def test_stats_basic(self) -> None:
        g = ImportGraph.from_adjacency(HUB)
        stats = g.stats()
        assert stats.node_count == 5
        assert stats.edge_count == 5  # auth,db,api,utils each import core; api imports auth
        assert stats.max_in_degree == 4  # core imported by 4
        assert "src/core.py" in stats.hub_files

    def test_isolated_nodes(self) -> None:
        g = ImportGraph.from_adjacency(ISOLATED)
        stats = g.stats()
        assert "src/orphan.py" in stats.isolated_nodes

    def test_empty_graph_stats(self) -> None:
        g = ImportGraph.empty()
        stats = g.stats()
        assert stats.node_count == 0
