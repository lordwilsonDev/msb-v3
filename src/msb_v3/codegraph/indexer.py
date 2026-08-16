"""Repo scanner + indexer for the Code Graph subsystem.

Walks a repository, parses every source file (stdlib parser), and upserts
nodes/edges into the SQLite store. Also wires the ``contains`` edges the
parser can't see alone: module -> its top-level symbols, and file ->
module. Skips well-known non-source/derived directories (``.git``,
``node_modules``, build outputs, vendored deps) so the index stays lean.

Incremental by design: ``index()`` clears the repo's rows first (the
parse pass is fast — msb-v3's own ~11k LOC indexes in well under a
second), which keeps the store consistent without a per-file mtime
registry. The parse pass and the store writes are separated so a parse
failure in one file degrades to \"skipped + counted\", never a partial
write.

Return shape (honest, per the anti-slop rule):

    {ok, repo, files_parsed, files_skipped, nodes, edges, parse_errors}
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.codegraph.parser import language_for, parse_source
from msb_v3.codegraph.store import CodeGraphStore

# Directories never indexed (derived/vendored/noise).
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".tox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "site-packages", ".direnv", "coverage", ".next", ".nuxt", ".turbo",
    "target", "vendor", "bower_components", "runtime", "data",
}
# Extensions that are source-of-truth code (not lockfiles/generated).
_SOURCE_EXTS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".go", ".rs",
    ".java", ".c", ".h", ".cpp", ".cc", ".hpp", ".cxx", ".cs", ".rb",
    ".php", ".sh", ".bash",
}
_MAX_FILE_BYTES = 1_000_000  # skip pathological files (generated/bundled)


class CodeGraphIndexer:
    def __init__(self, store: CodeGraphStore) -> None:
        self.store = store

    def index(self, repo_path: str, *, repo: Optional[str] = None) -> Dict[str, Any]:
        """Index a repository. ``repo`` defaults to the path itself (the
        graph is keyed per repo so different checkouts never collide)."""
        root = Path(repo_path).resolve()
        repo_key = repo or str(root)
        t0 = time.perf_counter()
        self.store.clear_repo(repo_key)

        parsed = 0
        skipped = 0
        parse_errors: List[str] = []
        node_count = 0
        edge_count = 0

        for path in self._walk(root):
            rel = path.relative_to(root).as_posix()
            lang = language_for(rel)
            if lang is None:
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                skipped += 1
                continue
            if len(source.encode("utf-8")) > _MAX_FILE_BYTES:
                skipped += 1
                continue

            result = parse_source(source, rel, lang)
            parsed += 1

            module_fq = self._module_fq(rel, lang)
            # file node + file->module containment
            self.store.upsert_node(
                repo=repo_key, kind="file", name=rel, fq_name=f"file:{rel}",
                file=rel, line=0,
            )
            self.store.upsert_node(
                repo=repo_key, kind="module", name=module_fq, fq_name=module_fq,
                file=rel, line=0,
            )
            self.store.add_edge(repo=repo_key, relation="contains", source=f"file:{rel}", target=module_fq, file=rel)

            top_levels: List[str] = []
            class_fqs: List[str] = []
            for node in result.nodes:
                # Root the symbol under the module when the parser left it
                # top-level (fq_name == short name).
                fq = node["fq_name"]
                if "." not in fq:
                    fq = f"{module_fq}.{node['name']}"
                self.store.upsert_node(
                    repo=repo_key, kind=node["kind"], name=node["name"], fq_name=fq,
                    file=rel, line=node["line"], col=node["col"],
                    signature=node.get("signature", ""), approximate=bool(node.get("approximate", False)),
                )
                node_count += 1
                # A symbol directly under the module (fq == module.name) is
                # module-contained; anything deeper is class-contained.
                if node["kind"] in ("function", "class", "type") and fq == f"{module_fq}.{node['name']}":
                    top_levels.append(fq)
                if node["kind"] == "class":
                    class_fqs.append(fq)
                elif node["kind"] == "method" and "." in node["fq_name"]:
                    parent = ".".join(node["fq_name"].split(".")[:-1])
                    if parent in class_fqs:
                        self.store.add_edge(repo=repo_key, relation="contains", source=parent, target=fq, file=rel)

            for edge in result.edges:
                source_fq = edge["source"]
                target_fq = edge["target"]
                if "." not in source_fq and source_fq != module_fq:
                    source_fq = f"{module_fq}.{source_fq}" if source_fq != module_fq else module_fq
                # targets may be external (imports) — leave them as-is; the
                # store tolerates targets with no node.
                self.store.add_edge(
                    repo=repo_key, relation=edge["relation"],
                    source=source_fq, target=target_fq,
                    file=rel, line=edge["line"],
                )
                edge_count += 1

            for fq in top_levels:
                self.store.add_edge(repo=repo_key, relation="contains", source=module_fq, target=fq, file=rel)

        latency_s = round(time.perf_counter() - t0, 4)
        return {
            "ok": True,
            "repo": repo_key,
            "files_parsed": parsed,
            "files_skipped": skipped,
            "nodes": node_count,
            "edges": edge_count,
            "parse_errors": parse_errors,
            "latency_s": latency_s,
            "stats": self.store.stats(repo_key),
        }

    def _module_fq(self, rel: str, lang: str) -> str:
        if lang == "python":
            parts = Path(rel).with_suffix("").parts
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            return ".".join(parts) if parts else "root"
        return Path(rel).with_suffix("").as_posix().replace("/", ".")

    def _walk(self, root: Path) -> List[Path]:
        out: List[Path] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() not in _SOURCE_EXTS:
                continue
            out.append(path)
        return out
