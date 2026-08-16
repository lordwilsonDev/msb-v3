"""Code Graph API — operator-gated repository intelligence (spec §4.2.1).

Agents (and dashboards) ask the graph questions about a repository:
symbol search, callers, callees, blast radius, context bundles, and
rename previews. Indexing a repo is the only write; everything else is a
read-only query. All routes are operator-gated — code structure is
sensitive even when the code itself is local.

Routes:

    POST /codegraph/index              (re)build the index for a repo
    GET  /codegraph/{repo}/stats       index stats
    GET  /codegraph/{repo}/symbol      search symbols (?name=)
    GET  /codegraph/{repo}/callers     who calls a symbol (?symbol=)
    GET  /codegraph/{repo}/callees     what a symbol calls (?symbol=)
    GET  /codegraph/{repo}/impact      blast radius (?file=&line=)
    GET  /codegraph/{repo}/context     definition + callers + callees (?symbol=)
    GET  /codegraph/{repo}/rename      rename preview (?name=)

``repo`` is a path-or-key; callers pass the same value they indexed with.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from msb_v3.api.auth import require_operator
from msb_v3.codegraph.indexer import CodeGraphIndexer
from msb_v3.codegraph.queries import CodeGraphQueries
from msb_v3.codegraph.store import CodeGraphStore
from msb_v3.core.config import settings

router = APIRouter()

_MAX_REPO_ARG = 512


class IndexRequest(BaseModel):
    path: str
    repo: str | None = None


def _store() -> CodeGraphStore:
    return CodeGraphStore(settings.codegraph_db_path)


def _queries() -> CodeGraphQueries:
    return CodeGraphQueries(_store())


def _repo_or_400(value: str) -> str:
    if not value or len(value) > _MAX_REPO_ARG:
        raise HTTPException(status_code=400, detail="repo must be a non-empty short string")
    return value


@router.post("/index", dependencies=[Depends(require_operator)])
async def index_repo(body: IndexRequest) -> Dict[str, Any]:
    """(Re)build the code graph for a repository. Operator-gated: indexing
    walks source on disk and persists derived data."""
    path = body.path.strip()
    if not path:
        raise HTTPException(status_code=422, detail="path is required")
    store = _store()
    indexer = CodeGraphIndexer(store)
    try:
        return indexer.index(path, repo=body.repo)
    except Exception as exc:  # noqa: BLE001 — surface the honest failure
        raise HTTPException(status_code=500, detail=f"index failed: {exc}") from exc


@router.get("/{repo:path}/stats", dependencies=[Depends(require_operator)])
async def repo_stats(repo: str) -> Dict[str, Any]:
    repo = _repo_or_400(repo)
    return {"ok": True, "stats": _store().stats(repo)}


@router.get("/{repo:path}/symbol", dependencies=[Depends(require_operator)])
async def find_symbol(repo: str, name: str, limit: int = 20) -> Dict[str, Any]:
    repo = _repo_or_400(repo)
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    return {"ok": True, "symbols": _queries().find_symbol(repo, name, limit=min(limit, 100))}


@router.get("/{repo:path}/callers", dependencies=[Depends(require_operator)])
async def callers(repo: str, symbol: str) -> Dict[str, Any]:
    repo = _repo_or_400(repo)
    if not symbol:
        raise HTTPException(status_code=422, detail="symbol is required")
    return {"ok": True, "symbol": symbol, "callers": _queries().callers_of(repo, symbol)}


@router.get("/{repo:path}/callees", dependencies=[Depends(require_operator)])
async def callees(repo: str, symbol: str) -> Dict[str, Any]:
    repo = _repo_or_400(repo)
    if not symbol:
        raise HTTPException(status_code=422, detail="symbol is required")
    return {"ok": True, "symbol": symbol, "callees": _queries().callees_of(repo, symbol)}


@router.get("/{repo:path}/impact", dependencies=[Depends(require_operator)])
async def impact(repo: str, file: str, line: int = 0) -> Dict[str, Any]:
    repo = _repo_or_400(repo)
    if not file:
        raise HTTPException(status_code=422, detail="file is required")
    return {"ok": True, "impact": _queries().impact_of(repo, file, line=line)}


@router.get("/{repo:path}/context", dependencies=[Depends(require_operator)])
async def context(repo: str, symbol: str, depth: int = 2) -> Dict[str, Any]:
    repo = _repo_or_400(repo)
    if not symbol:
        raise HTTPException(status_code=422, detail="symbol is required")
    return {"ok": True, "context": _queries().context_of(repo, symbol, depth=depth)}


@router.get("/{repo:path}/rename", dependencies=[Depends(require_operator)])
async def rename_preview(repo: str, name: str) -> Dict[str, Any]:
    repo = _repo_or_400(repo)
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    return {"ok": True, "rename": _queries().rename_preview(repo, name)}
