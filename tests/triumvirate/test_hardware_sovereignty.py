"""Tests for Triumvirate Phase 5 — Hardware Sovereignty."""
from __future__ import annotations

import os
import tempfile

from msb_v3.triumvirate import hardware_sovereignty as hw_mod
from msb_v3.triumvirate.hardware_sovereignty import ClusterAwareDiscovery, VectorHippocampus, PeerNode, DocumentChunk


def test_cluster_register_and_peers(tmp_path, monkeypatch):
    monkeypatch.setattr(hw_mod, "_RUNTIME_ROOT", tmp_path / "triumvirate")
    monkeypatch.setattr(hw_mod, "_MESH_STATE_FILE", tmp_path / "triumvirate" / "mesh_state.json")
    disco = ClusterAwareDiscovery()
    disco.register_peer(PeerNode(node_id="n1", host="mac-mini.local", port=8766, cluster_role="primary"))
    disco.register_peer(PeerNode(node_id="n2", host="mac-studio.local", port=8766, capacity=2, cluster_role="worker"))
    peers = disco.peers()
    assert len(peers) == 2
    assert {p["node_id"] for p in peers} == {"n1", "n2"}
    primary = next(p for p in peers if p["node_id"] == "n1")
    assert primary["cluster_role"] == "primary"


def test_vector_hippocampus_search(tmp_path):
    db_path = str(tmp_path / "hippocampus.db")
    hippocampus = VectorHippocampus(db_path=db_path)
    hippocampus.upsert(DocumentChunk(doc_id="doc1", chunk_id="c1", text="alpha", embedding=[1.0, 0.0]))
    hippocampus.upsert(DocumentChunk(doc_id="doc1", chunk_id="c2", text="beta", embedding=[0.0, 1.0]))
    results = hippocampus.search([1.0, 0.0], limit=2)
    assert len(results) == 2
    assert results[0]["text"] == "alpha"
    assert results[0]["score"] > results[1]["score"]
