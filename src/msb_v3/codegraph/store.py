"""SQLite graph store for the Code Graph subsystem.

Plain tables (schema.py) with provenance columns on every node and edge.
The store is intentionally dumb — insert/upsert/query — so the query
semantics (callers, callees, impact) live in queries.py where they are
unit-testable against the same store.

Concurrency: a per-instance lock serializes writes (single-process
uvicorn, same assumption as the rest of the codebase). Reads are
lock-free SQLite queries (WAL not enabled — the store is indexed in
bulk, then read; the default rollback journal is fine).

Honest contract: this is a static graph. ``approximate`` flags nodes
whose extraction used regex heuristics rather than a real AST.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.codegraph.schema import DDL


class CodeGraphStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # -- connection -----------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript(DDL)

    # -- nodes ----------------------------------------------------------

    def upsert_node(
        self,
        *,
        repo: str,
        kind: str,
        name: str,
        fq_name: str,
        file: str,
        line: int,
        col: int = 0,
        signature: str = "",
        approximate: bool = False,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO codegraph_nodes(repo, kind, name, fq_name, file, line, col, signature, approximate)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(repo, fq_name, kind) DO UPDATE SET
                    file = excluded.file, line = excluded.line, col = excluded.col,
                    signature = excluded.signature, approximate = excluded.approximate
                """,
                (repo, kind, name, fq_name, file, line, col, signature, int(approximate)),
            )

    def add_edge(
        self,
        *,
        repo: str,
        relation: str,
        source: str,
        target: str,
        file: str,
        line: int = 0,
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO codegraph_edges(repo, relation, source, target, file, line)
                VALUES (?,?,?,?,?,?)
                """,
                (repo, relation, source, target, file, line),
            )

    def get_node(self, repo: str, fq_name: str, kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
        q = "SELECT * FROM codegraph_nodes WHERE repo=? AND fq_name=?"
        args: List[Any] = [repo, fq_name]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        with self._conn() as conn:
            row = conn.execute(q, args).fetchone()
        return dict(row) if row else None

    def nodes_for_file(self, repo: str, file: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM codegraph_nodes WHERE repo=? AND file=? ORDER BY line",
                (repo, file),
            ).fetchall()
        return [dict(r) for r in rows]

    def search_nodes(self, repo: str, name: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Fuzzy symbol search: exact fq_name first, then exact-name, then
        case-sensitive prefix/contains matches (LIKE would fold case and
        make 'Engine' match the module 'engine')."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM codegraph_nodes
                WHERE repo=? AND (
                    fq_name = ? OR name = ?
                    OR fq_name GLOB ? OR name GLOB ?
                )
                ORDER BY
                  CASE
                    WHEN fq_name = ? THEN 0
                    WHEN name = ? THEN 1
                    WHEN fq_name GLOB ? THEN 2
                    ELSE 3
                  END,
                  line
                LIMIT ?
                """,
                (repo, name, name, f"{name}.*", f"*{name}*", name, name, f"{name}.*", limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def edges_from(self, repo: str, source: str, relation: Optional[str] = None) -> List[Dict[str, Any]]:
        q = "SELECT * FROM codegraph_edges WHERE repo=? AND source=?"
        args: List[Any] = [repo, source]
        if relation:
            q += " AND relation=?"
            args.append(relation)
        q += " ORDER BY file, line"
        with self._conn() as conn:
            rows = conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def edges_to(self, repo: str, target: str, relation: Optional[str] = None) -> List[Dict[str, Any]]:
        q = "SELECT * FROM codegraph_edges WHERE repo=? AND target=?"
        args: List[Any] = [repo, target]
        if relation:
            q += " AND relation=?"
            args.append(relation)
        q += " ORDER BY file, line"
        with self._conn() as conn:
            rows = conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def edges_in_file(self, repo: str, file: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM codegraph_edges WHERE repo=? AND file=? ORDER BY line",
                (repo, file),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- repo lifecycle -------------------------------------------------

    def clear_repo(self, repo: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM codegraph_edges WHERE repo=?", (repo,))
            conn.execute("DELETE FROM codegraph_nodes WHERE repo=?", (repo,))

    def repo_files(self, repo: str) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT file FROM codegraph_nodes WHERE repo=? ORDER BY file",
                (repo,),
            ).fetchall()
        return [r["file"] for r in rows]

    def stats(self, repo: str) -> Dict[str, Any]:
        with self._conn() as conn:
            nodes = conn.execute(
                "SELECT kind, COUNT(*) AS n FROM codegraph_nodes WHERE repo=? GROUP BY kind",
                (repo,),
            ).fetchall()
            edges = conn.execute(
                "SELECT relation, COUNT(*) AS n FROM codegraph_edges WHERE repo=? GROUP BY relation",
                (repo,),
            ).fetchall()
        return {
            "repo": repo,
            "nodes": sum(r["n"] for r in nodes),
            "edges": sum(r["n"] for r in edges),
            "nodes_by_kind": {r["kind"]: r["n"] for r in nodes},
            "edges_by_relation": {r["relation"]: r["n"] for r in edges},
        }
