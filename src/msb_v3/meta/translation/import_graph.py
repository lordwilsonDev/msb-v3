"""ImportGraph — code-structure-aware context selection.

META-2 adds import-graph intelligence to the ContextCompiler.  Instead of
relying solely on keyword matching and same-directory heuristics, the
ContextCompiler can now understand:

    - transitive dependencies (what does this file *actually* need?)
    - reverse dependencies (who *depends on* this file?)
    - graph distance from seed files (how close is this to the task's core?)
    - hub files (high-connectivity files that many things import)
    - impact zones (what would break if this file changed?)
    - module clustering (files that form a cohesive unit)

Architecture:
    Adjacency Dict (file → [direct deps])
            ↓
    ImportGraph (immutable snapshot)
            ↓
    Queries:
        transitive_deps(file) → set[str]
        reverse_deps(file) → set[str]
        distance(seed, target) → int | None
        hub_files() → list[str]
        impact_zone(file) → set[str]
        cluster(seed) → set[str]
        relevance_scores(seed_files, all_files) → dict[str, float]

The graph does NOT parse source code — it consumes a pre-built adjacency
dict.  This keeps it testable and decoupled from the filesystem.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Maximum BFS depth to prevent runaway expansion.
_MAX_DEPTH = 10


@dataclass(frozen=True)
class GraphStats:
    """Aggregate statistics for the import graph."""

    node_count: int = 0
    edge_count: int = 0
    avg_out_degree: float = 0.0
    avg_in_degree: float = 0.0
    max_out_degree: int = 0
    max_in_degree: int = 0
    hub_files: List[str] = field(default_factory=list)
    isolated_nodes: List[str] = field(default_factory=list)


class ImportGraph:
    """Immutable snapshot of the code dependency graph.

    Build from a pre-computed adjacency dict, then query for relevance,
    transitive deps, impact zones, and hub detection.

    Usage::

        graph = ImportGraph.from_adjacency({
            "src/auth.py": ["src/crypto.py", "src/db.py"],
            "src/crypto.py": [],
            "src/db.py": ["src/config.py"],
        })

        graph.transitive_deps("src/auth.py")
        # → {"src/crypto.py", "src/db.py", "src/config.py"}

        graph.reverse_deps("src/config.py")
        # → {"src/db.py", "src/auth.py"}

        graph.relevance_scores(
            seed_files=["src/auth.py"],
            candidate_files=["src/crypto.py", "src/db.py", "src/config.py", "src/unrelated.py"],
        )
        # → {"src/crypto.py": 0.9, "src/db.py": 0.8, "src/config.py": 0.7, "src/unrelated.py": 0.0}
    """

    def __init__(self, adjacency: Dict[str, List[str]]) -> None:
        """Build from an adjacency dict: file → [direct dependencies]."""
        self._adj: Dict[str, List[str]] = {k: list(v) for k, v in adjacency.items()}
        self._reverse: Dict[str, Set[str]] = self._build_reverse()
        self._all_nodes: Set[str] = set(self._adj.keys()) | set(self._reverse.keys())
        self._stats: Optional[GraphStats] = None

    @classmethod
    def from_adjacency(cls, adjacency: Dict[str, List[str]]) -> ImportGraph:
        """Construct from a file→[deps] adjacency dict."""
        return cls(adjacency)

    @classmethod
    def empty(cls) -> ImportGraph:
        """Construct an empty graph."""
        return cls({})

    # -- Core queries --------------------------------------------------------

    def nodes(self) -> Set[str]:
        """All known files in the graph."""
        return set(self._all_nodes)

    def direct_deps(self, file: str) -> Set[str]:
        """Direct dependencies of *file*."""
        return set(self._adj.get(file, []))

    def direct_importers(self, file: str) -> Set[str]:
        """Files that directly import *file*."""
        return set(self._reverse.get(file, set()))

    def transitive_deps(self, file: str, *, max_depth: int = _MAX_DEPTH) -> Set[str]:
        """All transitive dependencies of *file* (BFS).

        Returns the set of files reachable from *file* following dependency
        edges, up to *max_depth* hops.
        """
        visited: Set[str] = set()
        queue: deque[tuple[str, int]] = deque([(file, 0)])
        while queue:
            current, depth = queue.popleft()
            if current in visited or depth > max_depth:
                continue
            visited.add(current)
            for dep in self._adj.get(current, []):
                if dep not in visited:
                    queue.append((dep, depth + 1))
        visited.discard(file)  # don't include self
        return visited

    def transitive_importers(self, file: str, *, max_depth: int = _MAX_DEPTH) -> Set[str]:
        """All files that transitively depend on *file* (reverse BFS)."""
        visited: Set[str] = set()
        queue: deque[tuple[str, int]] = deque([(file, 0)])
        while queue:
            current, depth = queue.popleft()
            if current in visited or depth > max_depth:
                continue
            visited.add(current)
            for importer in self._reverse.get(current, set()):
                if importer not in visited:
                    queue.append((importer, depth + 1))
        visited.discard(file)
        return visited

    def distance(self, seed: str, target: str, *, max_depth: int = _MAX_DEPTH) -> Optional[int]:
        """Shortest path distance from *seed* to *target* via dependency edges.

        Returns None if *target* is not reachable from *seed*.
        """
        if seed == target:
            return 0
        visited: Dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque([(seed, 0)])
        while queue:
            current, depth = queue.popleft()
            if current in visited or depth > max_depth:
                continue
            visited[current] = depth
            if current == target:
                return depth
            for dep in self._adj.get(current, []):
                if dep not in visited:
                    queue.append((dep, depth + 1))
        return None

    def cluster(self, seed: str, *, max_depth: int = 3) -> Set[str]:
        """Files reachable within *max_depth* hops from *seed* in either direction.

        This identifies a cohesive module/package around a seed file.
        """
        forward = self.transitive_deps(seed, max_depth=max_depth)
        backward = self.transitive_importers(seed, max_depth=max_depth)
        return {seed} | forward | backward

    def impact_zone(self, file: str, *, max_depth: int = _MAX_DEPTH) -> Set[str]:
        """Files that would be affected if *file* changed (reverse transitive deps)."""
        return self.transitive_importers(file, max_depth=max_depth)

    def hub_files(self, top_n: int = 10) -> List[str]:
        """Files with the highest total degree (in + out).

        Hub files are high-connectivity files that many things import
        or that import many things.  These are typically core modules,
        __init__.py files, or shared utilities.
        """
        if self._stats is None:
            self._stats = self._compute_stats()
        return self._stats.hub_files[:top_n]

    def relevance_scores(
        self,
        seed_files: List[str],
        candidate_files: List[str],
        *,
        max_distance: int = 5,
    ) -> Dict[str, float]:
        """Score candidate files by graph distance from seed files.

        Scoring algorithm:
            - Direct deps of seeds: 0.95
            - Distance 2: 0.85
            - Distance 3: 0.75
            - ...
            - Distance d: max(0.1, 0.95 - 0.1 * (d-1))
            - Not reachable: 0.0
            - Reverse deps of seeds: bonus +0.1 (who imports the seeds matters)
        """
        scores: Dict[str, float] = {}

        # Forward scores (seed → candidate).
        seed_set = set(seed_files)
        for candidate in candidate_files:
            if candidate in seed_set:
                scores[candidate] = 1.0  # seed itself is maximally relevant
                continue

            best_distance: Optional[int] = None
            for seed in seed_files:
                d = self.distance(seed, candidate, max_depth=max_distance)
                if d is not None:
                    if best_distance is None or d < best_distance:
                        best_distance = d

            if best_distance is not None:
                scores[candidate] = max(0.1, 0.95 - 0.1 * (best_distance - 1))
            else:
                scores[candidate] = 0.0

        # Reverse-dep bonus: files that import the seeds are also relevant.
        for candidate in candidate_files:
            if candidate in seed_set:
                continue
            for seed in seed_files:
                if candidate in self._reverse.get(seed, set()):
                    scores[candidate] = min(1.0, scores.get(candidate, 0.0) + 0.1)
                    break

        return scores

    def stats(self) -> GraphStats:
        """Compute and cache aggregate graph statistics."""
        if self._stats is None:
            self._stats = self._compute_stats()
        return self._stats

    # -- Internal ------------------------------------------------------------

    def _build_reverse(self) -> Dict[str, Set[str]]:
        """Build reverse adjacency: file → {files that import it}."""
        reverse: Dict[str, Set[str]] = {}
        for file, deps in self._adj.items():
            for dep in deps:
                if dep not in reverse:
                    reverse[dep] = set()
                reverse[dep].add(file)
        return reverse

    def _compute_stats(self) -> GraphStats:
        """Compute aggregate graph statistics."""
        out_degrees = {n: len(self._adj.get(n, [])) for n in self._all_nodes}
        in_degrees = {n: len(self._reverse.get(n, set())) for n in self._all_nodes}

        total_out = sum(out_degrees.values())
        total_in = sum(in_degrees.values())
        n = len(self._all_nodes) or 1

        # Hub files = highest total degree.
        total_degrees = {n: out_degrees[n] + in_degrees[n] for n in self._all_nodes}
        sorted_nodes = sorted(total_degrees, key=lambda n: total_degrees[n], reverse=True)

        isolated = [n for n in self._all_nodes if out_degrees[n] == 0 and in_degrees[n] == 0]

        return GraphStats(
            node_count=len(self._all_nodes),
            edge_count=total_out,
            avg_out_degree=total_out / n,
            avg_in_degree=total_in / n,
            max_out_degree=max(out_degrees.values()) if out_degrees else 0,
            max_in_degree=max(in_degrees.values()) if in_degrees else 0,
            hub_files=sorted_nodes[:10],
            isolated_nodes=sorted(isolated)[:10],
        )
