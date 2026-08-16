"""Code Graph schema — node/edge kinds and SQLite DDL.

Schema mirrors the spec §4.2.1: nodes (files, modules, classes, functions,
methods, types, routes) + edges (calls, imports, inherits, references,
contains). Storage is plain SQLite graph tables — zero new dependencies,
fully auditable, indexable for the <1s query gate.

Node kinds:

    file      a source file (kind=file; name = relative path)
    module    an importable unit (python module / js module / crate …)
    class     a class (name = short name; fq_name = dotted path)
    function  a function or method (name = short; fq_name = dotted path)
    type      a type alias / interface / struct (non-class named type)
    route     a framework route when the parser can see one (fastapi etc.)

Edge relations:

    contains  parent -> child (module -> class, class -> method, file -> top)
    calls     caller -> callee (static best-effort resolution)
    imports   importer -> imported (module-level)
    inherits  subclass -> base (class edges)
    references symbol -> symbol (a name use that isn't a call/import)

Every node carries (repo, file, line, col, kind, name, fq_name) and every
edge (repo, source, target, relation, file, line) — provenance for the
AuditChain and for the impact-analysis blast radius.
"""

from __future__ import annotations

NODE_KINDS = ("file", "module", "class", "function", "method", "type", "route")
EDGE_RELATIONS = ("contains", "calls", "imports", "inherits", "references")

DDL = """
CREATE TABLE IF NOT EXISTS codegraph_nodes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    repo       TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('file','module','class','function','method','type','route')),
    name       TEXT NOT NULL,
    fq_name    TEXT NOT NULL,
    file       TEXT NOT NULL,
    line       INTEGER NOT NULL,
    col        INTEGER NOT NULL DEFAULT 0,
    signature  TEXT NOT NULL DEFAULT '',
    approximate INTEGER NOT NULL DEFAULT 0,
    UNIQUE (repo, fq_name, kind)
);

CREATE TABLE IF NOT EXISTS codegraph_edges (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    repo     TEXT NOT NULL,
    relation TEXT NOT NULL CHECK (relation IN ('contains','calls','imports','inherits','references')),
    source   TEXT NOT NULL,
    target   TEXT NOT NULL,
    file     TEXT NOT NULL,
    line     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (repo, relation, source, target, file, line)
);

CREATE INDEX IF NOT EXISTS idx_nodes_repo_fq    ON codegraph_nodes(repo, fq_name);
CREATE INDEX IF NOT EXISTS idx_nodes_repo_file  ON codegraph_nodes(repo, file);
CREATE INDEX IF NOT EXISTS idx_edges_repo_rel   ON codegraph_edges(repo, relation);
CREATE INDEX IF NOT EXISTS idx_edges_source     ON codegraph_edges(repo, source);
CREATE INDEX IF NOT EXISTS idx_edges_target     ON codegraph_edges(repo, target);
CREATE INDEX IF NOT EXISTS idx_edges_file       ON codegraph_edges(repo, file);
"""
