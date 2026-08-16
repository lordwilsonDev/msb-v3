"""Graph queries for the Code Graph subsystem (spec §4.2.1).

The query surface an agent actually uses:

    find_symbol(name)      -> candidates (search, not exact)
    callers_of(symbol)     -> who calls this symbol (G1 gate: <1s)
    callees_of(symbol)     -> what this symbol calls
    impact_of(file, line)  -> blast radius: symbols in the file + their
                              transitive callers
    context_of(symbol)     -> definition + callers + callees bundle
    rename_preview(name)   -> every reference that a rename would touch

All queries are read-only over the SQLite store and return plain dicts
with provenance (file/line) so the caller can point at evidence. ``repo``
is explicit everywhere — the graph is keyed per repo.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from msb_v3.codegraph.store import CodeGraphStore

# relations that mean "this code touches the symbol"
_REFERENCE_RELATIONS = ("calls", "references", "imports")


class CodeGraphQueries:
    def __init__(self, store: CodeGraphStore) -> None:
        self.store = store

    # -- symbol search ---------------------------------------------------

    def find_symbol(self, repo: str, name: str, *, limit: int = 20) -> List[Dict[str, Any]]:
        return self.store.search_nodes(repo, name, limit=limit)

    def symbol(self, repo: str, fq_name: str) -> Optional[Dict[str, Any]]:
        return self.store.get_node(repo, fq_name)

    # -- call graph ------------------------------------------------------

    def callers_of(self, repo: str, symbol: str) -> List[Dict[str, Any]]:
        """Who calls ``symbol`` — the G1 gate query. Resolves by fq_name
        first, then falls back to any symbol whose short name matches
        (cross-module calls resolve by short name since imports alias)."""
        exact = self.store.edges_to(repo, symbol, relation="calls")
        if exact:
            return exact
        # fallback: edges targeting any node named ``symbol``
        targets = self._resolve_targets(repo, symbol)
        if not targets:
            return []
        seen: Dict[str, Dict[str, Any]] = {}
        for t in targets:
            for e in self.store.edges_to(repo, t, relation="calls"):
                seen[e["id"]] = e
        return sorted(seen.values(), key=lambda e: (e["file"], e["line"]))

    def callees_of(self, repo: str, symbol: str) -> List[Dict[str, Any]]:
        return self.store.edges_from(repo, symbol, relation="calls")

    def references_of(self, repo: str, symbol: str) -> List[Dict[str, Any]]:
        """Every edge that touches the symbol (calls + references + imports).
        Resolves short names to their fq targets first (a symbol passed to
        Depends() produces references, not calls — both are wanted here)."""
        targets = self._resolve_targets(repo, symbol)
        out: List[Dict[str, Any]] = []
        for t in targets:
            for rel in _REFERENCE_RELATIONS:
                out.extend(self.store.edges_to(repo, t, relation=rel))
        return sorted(out, key=lambda e: (e["file"], e["line"]))

    def _resolve_targets(self, repo: str, symbol: str) -> set[str]:
        """Exact fq, else every node whose short name matches the symbol."""
        if self.store.get_node(repo, symbol):
            return {symbol}
        nodes = self.store.search_nodes(repo, symbol, limit=50)
        return {n["fq_name"] for n in nodes if n["name"] == symbol}

    # -- impact analysis --------------------------------------------------

    def impact_of(self, repo: str, file: str, line: int = 0) -> Dict[str, Any]:
        """Blast radius of a change at (file[, line]).

        Collects every symbol defined in the file (or the one at ``line``),
        then walks callers transitively (2 hops by default) so the report
        shows both direct dependents and downstream consumers. Everything
        carries file/line provenance.
        """
        nodes = self.store.nodes_for_file(repo, file)
        if line:
            # the symbol at (or nearest above) the line; if none (e.g. a
            # docstring line), fall back to the file's symbols so the blast
            # radius still means something.
            at_line = [n for n in nodes if 0 < n["line"] <= line]
            seeds = [max(at_line, key=lambda n: n["line"])] if at_line else nodes
        else:
            seeds = nodes

        reachable: Dict[str, Dict[str, Any]] = {}
        frontier = [n["fq_name"] for n in seeds if n["kind"] not in ("file", "module")]
        seen: set[str] = set()
        for _hop in range(2):
            nxt: List[str] = []
            for fq in frontier:
                if fq in seen:
                    continue
                seen.add(fq)
                for e in self.store.edges_to(repo, fq, relation="calls"):
                    caller_node = self.store.get_node(repo, e["source"])
                    reachable[e["source"]] = {
                        "symbol": e["source"],
                        "file": caller_node["file"] if caller_node else e["file"],
                        "line": caller_node["line"] if caller_node else e["line"],
                        "kind": caller_node["kind"] if caller_node else "?",
                        "hop": _hop + 1,
                    }
                    nxt.append(e["source"])
            frontier = nxt

        return {
            "file": file,
            "line": line or None,
            "seeds": [n["fq_name"] for n in seeds if n["kind"] not in ("file", "module")],
            "direct": len(reachable),
            "dependents": sorted(reachable.values(), key=lambda d: (d["hop"], d["file"], d["line"])),
        }

    # -- context bundle ---------------------------------------------------

    def context_of(self, repo: str, symbol: str, *, depth: int = 2) -> Dict[str, Any]:
        """One symbol's definition + callers + callees — enough to reason
        about it without loading the file. ``depth`` controls how many
        caller hops are included (default 2)."""
        node = self.symbol(repo, symbol)
        if node is None:
            # short-name lookup: resolve to the exact-name fq, else candidates
            matches = self.find_symbol(repo, symbol, limit=5)
            exact = [m for m in matches if m["name"] == symbol]
            if exact:
                symbol = exact[0]["fq_name"]
                node = self.symbol(repo, symbol)
            if node is None:
                return {"found": False, "symbol": symbol, "candidates": matches}

        callers = self.callers_of(repo, symbol)
        callees = self.callees_of(repo, symbol)
        refs = self.references_of(repo, symbol)

        def _annotate(edges: List[Dict[str, Any]], *, end: str) -> List[Dict[str, Any]]:
            """Annotate one edge endpoint with node metadata + provenance.
            Callers annotate the source (who calls); callees the target (who
            is called)."""
            out = []
            for e in edges:
                other = e[end]
                n = self.store.get_node(repo, other)
                out.append(
                    {
                        "symbol": other,
                        "file": e["file"],
                        "line": e["line"],
                        "kind": n["kind"] if n else "?",
                        "signature": n.get("signature", "") if n else "",
                    }
                )
            return out

        # dedupe by symbol (callers: the source; callees: the target)
        caller_map: Dict[str, Dict[str, Any]] = {}
        for c in _annotate(callers, end="source"):
            caller_map.setdefault(c["symbol"], c)
        callee_map: Dict[str, Dict[str, Any]] = {}
        for c in _annotate(callees, end="target"):
            callee_map.setdefault(c["symbol"], c)

        return {
            "found": True,
            "symbol": symbol,
            "kind": node["kind"],
            "file": node["file"],
            "line": node["line"],
            "signature": node.get("signature", ""),
            "approximate": bool(node.get("approximate", 0)),
            "callers": sorted(caller_map.values(), key=lambda c: (c["file"], c["line"])),
            "callees": sorted(callee_map.values(), key=lambda c: (c["file"], c["line"])),
            "references": refs,
        }

    # -- rename preview ---------------------------------------------------

    def rename_preview(self, repo: str, name: str) -> Dict[str, Any]:
        """Everything a rename of ``name`` would touch: the definition(s)
        and every call/reference edge pointing at them. This is a preview —
        it never mutates."""
        nodes = [n for n in self.store.search_nodes(repo, name, limit=50) if n["name"] == name]
        targets = {n["fq_name"] for n in nodes}
        refs: List[Dict[str, Any]] = []
        for t in targets:
            refs.extend(self.references_of(repo, t))
        return {
            "name": name,
            "definitions": nodes,
            "reference_count": len(refs),
            "references": sorted(refs, key=lambda e: (e["file"], e["line"])),
        }
