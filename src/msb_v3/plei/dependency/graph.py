"""Dependency Graph — module graph, critical path, bottlenecks, coupling.

Builds a directed graph from source-import data and answers:
    1. What is the critical path? (longest topological chain)
    2. Which modules are bottlenecks? (highest fan-in)
    3. How coupled is the codebase? (fan-in/out ratios, stability metrics)
    4. What cyclic dependencies exist?

Algorithm: Kahn's topological sort + longest-path for critical path.
All metrics are deterministic — no ML, no heuristics.
"""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Node:
    """One module/package in the dependency graph."""

    name: str
    fan_in: int = 0  # how many modules depend on this one
    fan_out: int = 0  # how many modules this one depends on
    file_count: int = 0
    line_count: int = 0


@dataclass(slots=True)
class Edge:
    """A dependency edge: source → target."""

    source: str
    target: str


@dataclass
class DependencyGraph:
    """Directed graph of project-internal dependencies."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    critical_path: list[str] = field(default_factory=list)
    critical_path_length: int = 0
    bottlenecks: list[dict[str, Any]] = field(default_factory=list)
    coupling_score: float = 0.0  # 0.0–1.0, lower is better (less coupled)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)


def _collect_imports(file_path: Path) -> list[str]:
    try:
        tree = ast.parse(file_path.read_text())
    except (SyntaxError, FileNotFoundError):
        return []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split(".")[0])
    return imports


def build_dependency_graph(project_root: str | Path) -> DependencyGraph:
    """Build the dependency graph from source-import data.

    Only tracks project-internal dependencies (imports from msb_v3, msb_ledger,
    etc.) — stdlib and third-party imports are excluded.
    """
    root = Path(project_root).resolve()
    src_dir = root / "src"
    graph = DependencyGraph()

    if not src_dir.is_dir():
        return graph

    # Discover project packages
    packages: set[str] = set()
    for d in src_dir.iterdir():
        if d.is_dir() and (d / "__init__.py").exists() and not d.name.startswith("_"):
            packages.add(d.name)

    if not packages:
        return graph

    # Build node data and adjacency
    adjacency: dict[str, set[str]] = {p: set() for p in packages}
    reverse_adj: dict[str, set[str]] = {p: set() for p in packages}

    for pkg in sorted(packages):
        pkg_dir = src_dir / pkg
        py_files = list(pkg_dir.rglob("*.py"))
        node = Node(name=pkg)
        node.file_count = len(py_files)
        total_lines = 0
        all_imports: set[str] = set()

        for f in py_files:
            try:
                total_lines += len(f.read_text().split("\n"))
            except Exception:
                pass
            all_imports.update(_collect_imports(f))

        node.line_count = total_lines
        internal_imports = all_imports & packages
        # Exclude self-imports
        internal_imports.discard(pkg)
        node.fan_out = len(internal_imports)
        adjacency[pkg] = internal_imports

        for dep in sorted(internal_imports):
            graph.edges.append(Edge(source=pkg, target=dep))

        graph.nodes[pkg] = node

    # Compute fan-in from reverse adjacency
    for pkg, deps in adjacency.items():
        for dep in deps:
            reverse_adj.setdefault(dep, set()).add(pkg)

    for pkg, node in graph.nodes.items():
        node.fan_in = len(reverse_adj.get(pkg, set()))

    # Detect cycles (DFS with coloring)
    graph.cycles = _detect_cycles(packages, adjacency)

    # Critical path — longest path in DAG (or longest simple path with cycles)
    if not graph.cycles:
        graph.critical_path, graph.critical_path_length = _critical_path(
            packages, adjacency
        )
    else:
        # With cycles, find longest simple path through the graph
        graph.critical_path = []
        graph.critical_path_length = 0
        for start in packages:
            path, length = _longest_simple_path(start, packages, adjacency)
            if length > graph.critical_path_length:
                graph.critical_path = path
                graph.critical_path_length = length

    # Bottlenecks — top 5 by fan-in
    sorted_nodes = sorted(graph.nodes.values(), key=lambda n: -n.fan_in)
    graph.bottlenecks = [
        {
            "module": n.name,
            "fan_in": n.fan_in,
            "fan_out": n.fan_out,
            "file_count": n.file_count,
            "line_count": n.line_count,
            "instability": _instability(n),
        }
        for n in sorted_nodes[:5]
    ]

    # Coupling score — average instability variance (lower = less coupled = better)
    if graph.nodes:
        instabilities = [_instability(n) for n in graph.nodes.values()]
        avg_inst = sum(instabilities) / len(instabilities)
        variance = sum((i - avg_inst) ** 2 for i in instabilities) / len(instabilities)
        # Invert: low variance + moderate instability = low coupling
        graph.coupling_score = round(1.0 - min(variance, 0.25) * 4, 2)

    return graph


# --- Internal algorithms ---

def _detect_cycles(nodes: set[str], adj: dict[str, set[str]]) -> list[list[str]]:
    """Detect all simple cycles using DFS coloring (WHITE/GRAY/BLACK)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in nodes}
    cycles: list[list[str]] = []
    stack: list[str] = []

    def dfs(v: str) -> None:
        if color[v] == BLACK:
            return
        if color[v] == GRAY:
            # Found a cycle
            cycle_start = stack.index(v)
            cycles.append(list(stack[cycle_start:]))
            return
        color[v] = GRAY
        stack.append(v)
        for neighbor in sorted(adj.get(v, set())):
            if neighbor in color:
                dfs(neighbor)
        stack.pop()
        color[v] = BLACK

    for node in sorted(nodes):
        if color[node] == WHITE:
            dfs(node)

    return cycles


def _critical_path(
    nodes: set[str], adj: dict[str, set[str]]
) -> tuple[list[str], int]:
    """Longest path in a DAG via Kahn's topological sort + DP.

    Returns (path_nodes, path_length).
    """
    in_degree = {n: 0 for n in nodes}
    for src, targets in adj.items():
        for tgt in targets:
            if tgt in in_degree:
                in_degree[tgt] += 1

    queue: deque[str] = deque(n for n in nodes if in_degree[n] == 0)
    topo_order: list[str] = []
    while queue:
        v = queue.popleft()
        topo_order.append(v)
        for neighbor in adj.get(v, set()):
            if neighbor in in_degree:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

    # DP: longest path to each node
    dist: dict[str, int] = {n: 0 for n in nodes}
    prev: dict[str, str | None] = {n: None for n in nodes}

    for v in topo_order:
        for neighbor in adj.get(v, set()):
            if neighbor in dist and dist[v] + 1 > dist[neighbor]:
                dist[neighbor] = dist[v] + 1
                prev[neighbor] = v

    # Find the farthest node
    end = max(dist, key=lambda k: dist.get(k, 0)) if dist else ""
    path_length = dist.get(end, 0)

    # Reconstruct path
    path: list[str] = []
    current: str | None = end
    while current is not None and current in prev:
        path.append(current)
        current = prev.get(current)
    path.reverse()

    return path, path_length


def _longest_simple_path(
    start: str, nodes: set[str], adj: dict[str, set[str]]
) -> tuple[list[str], int]:
    """Longest simple path from a given start node via DFS.

    Returns (path, length). Handles graphs with cycles.
    """
    best_path: list[str] = []
    best_len = 0

    def dfs(v: str, visited: set[str], path: list[str]) -> None:
        nonlocal best_path, best_len
        visited.add(v)
        path.append(v)
        if len(path) > best_len:
            best_len = len(path)
            best_path = list(path)
        for neighbor in adj.get(v, set()):
            if neighbor not in visited and neighbor in nodes:
                dfs(neighbor, visited, path)
        path.pop()
        visited.discard(v)

    dfs(start, set(), [])
    return best_path, best_len - 1  # length in edges, not nodes


def _instability(node: Node) -> float:
    """Martin's instability metric: fan-out / (fan-in + fan-out).

    I = 0 → maximally stable (lots of dependents, few dependencies)
    I = 1 → maximally unstable (many dependencies, no dependents)
    NaN for isolated nodes → 0.
    """
    total = node.fan_in + node.fan_out
    if total == 0:
        return 0.0
    return round(node.fan_out / total, 2)


# --- Output helpers ---

def dependency_graph_as_dict(graph: DependencyGraph) -> dict[str, Any]:
    return {
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "cycles": graph.cycles,
        "cycle_count": len(graph.cycles),
        "critical_path": graph.critical_path,
        "critical_path_length": graph.critical_path_length,
        "bottlenecks": graph.bottlenecks,
        "coupling_score": graph.coupling_score,
        "nodes": [
            {
                "name": n.name,
                "fan_in": n.fan_in,
                "fan_out": n.fan_out,
                "file_count": n.file_count,
                "line_count": n.line_count,
                "instability": round(_instability(n), 2),
            }
            for n in sorted(graph.nodes.values(), key=lambda n: n.name)
        ],
    }