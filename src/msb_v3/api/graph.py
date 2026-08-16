"""Knowledge-graph indexing over memory store."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from msb_v3.core.container import ApplicationContainer, get_container_dep
from msb_v3.memory.store import Message

router = APIRouter()

_GRAPH_DIR = Path(os.getenv("MSB_GRAPH_DIR", "data/memory_graph"))
_GRAPH_DIR.mkdir(parents=True, exist_ok=True)


class GraphNode(BaseModel):
    id: str
    label: str
    type: str = "entity"
    count: int = 1
    sessions: List[str] = Field(default_factory=list)


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: str
    weight: int = 1


class GraphIngest(BaseModel):
    session: str
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[A-Za-z][A-Za-z0-9_/-]+", text.lower())


def _session_graph_path(session: str) -> Path:
    return _GRAPH_DIR / f"{session}.json"


def _load_graph(session: str) -> Dict[str, Any]:
    path = _session_graph_path(session)
    if not path.exists():
        return {"nodes": {}, "edges": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_graph(session: str, graph: Dict[str, Any]) -> None:
    _GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    _session_graph_path(session).write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _upsert_node(graph: Dict[str, Any], node_id: str, label: str, session: str) -> Dict[str, Any]:
    nodes = graph.setdefault("nodes", {})
    node = nodes.get(node_id)
    if node is None:
        node = {"id": node_id, "label": label, "type": "entity", "count": 0, "sessions": []}
        nodes[node_id] = node
    node["count"] = node.get("count", 0) + 1
    if session not in node.get("sessions", []):
        node.setdefault("sessions", []).append(session)
    return node


def _upsert_edge(graph: Dict[str, Any], source: str, target: str, relation: str) -> Dict[str, Any]:
    edges = graph.setdefault("edges", [])
    for edge in edges:
        if edge["source"] == source and edge["target"] == target and edge["relation"] == relation:
            edge["weight"] = edge.get("weight", 1) + 1
            return edge
    new_edge = {"source": source, "target": target, "relation": relation, "weight": 1}
    edges.append(new_edge)
    return new_edge


def _ingest_into_graph(session: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    graph = _load_graph(session)
    tokens = _tokenize(text)
    if not tokens:
        return graph

    for token in tokens:
        _upsert_node(graph, token, token, session)

    cooccurrence: Counter[Tuple[str, str]] = Counter()
    for i in range(len(tokens) - 1):
        cooccurrence[(tokens[i], tokens[i + 1])] += 1

    for (a, b), weight in cooccurrence.most_common(50):
        _upsert_edge(graph, a, b, "co-occurs-with")
        _upsert_edge(graph, b, a, "co-occurs-with")

    if metadata:
        for key in list(metadata.keys())[:20]:
            val = str(metadata[key]).strip().lower()
            if not val:
                continue
            _upsert_node(graph, val, f"{key}:{val}", session)
            _upsert_edge(graph, session, val, "has-metadata")

    _save_graph(session, graph)
    return graph


@router.post("/ingest")
def ingest_graph(
    payload: GraphIngest,
    container: ApplicationContainer = Depends(get_container_dep),
) -> Dict[str, Any]:
    message = Message(role="user", content=payload.text, tokens=0)
    container.memory_store.append(payload.session, message)
    graph = _ingest_into_graph(payload.session, payload.text, payload.metadata)
    return {
        "ok": True,
        "session": payload.session,
        "nodes": len(graph.get("nodes", {})),
        "edges": len(graph.get("edges", [])),
    }


@router.get("")
def list_graph_sessions() -> Dict[str, Any]:
    sessions = sorted(p.stem for p in _GRAPH_DIR.glob("*.json") if p.is_file())
    return {"ok": True, "sessions": sessions}


@router.get("/{session}")
def get_graph(session: str) -> Dict[str, Any]:
    graph = _load_graph(session)
    return {
        "ok": True,
        "session": session,
        "nodes": list(graph.get("nodes", {}).values()),
        "edges": graph.get("edges", []),
    }


@router.get("/{session}/top")
def get_graph_top(session: str, k: int = 20) -> Dict[str, Any]:
    graph = _load_graph(session)
    nodes = sorted(
        graph.get("nodes", {}).values(),
        key=lambda n: n.get("count", 0),
        reverse=True,
    )[:k]
    return {"ok": True, "session": session, "top": nodes}
