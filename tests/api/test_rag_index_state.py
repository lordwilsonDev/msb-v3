"""Tests for immutable RAG generations and source-manifest currentness."""
from __future__ import annotations

import hashlib

import pytest

from msb_v3.api import rag_index_state as state


def _entry(source: str, text: str, source_text: str | None = None, chunk: int = 0) -> dict:
    return {
        "source": source,
        "chunk": chunk,
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "source_sha256": hashlib.sha256((source_text or text).encode()).hexdigest(),
    }


def _doc(entry: dict, text: str) -> dict:
    return {"source": entry["source"], "chunk": entry["chunk"], "text": text,
            "source_sha256": entry["source_sha256"]}


def _use_db(monkeypatch, tmp_path):
    monkeypatch.setattr(state.settings, "db_path", str(tmp_path / "state.db"))


def _publish(tenant: str, manifest: list[dict], docs: list[dict]) -> tuple[dict, str | None]:
    snap = state.begin_snapshot(tenant, manifest)
    claimed = state.claim_snapshot_batch(snap["snapshot_id"], tenant, docs)
    state.record_uploaded(snap["snapshot_id"], tenant, claimed)
    return state.commit_snapshot(snap["snapshot_id"], tenant, len(docs))


def test_manifest_is_order_independent_and_binds_chunk_and_source_hash():
    a = _entry("notes/a.md", "chunk A", "full original")
    b = _entry("notes/a.md", "chunk B", "full original", chunk=1)
    assert state.manifest_sha256([a, b]) == state.manifest_sha256([b, a])
    changed_a = {**a, "source_sha256": hashlib.sha256(b"changed source").hexdigest()}
    changed_b = {**b, "source_sha256": changed_a["source_sha256"]}
    assert state.manifest_sha256([a, b]) != state.manifest_sha256([changed_a, changed_b])


@pytest.mark.parametrize("source", ["../outside.md", "/absolute.md", "./dot.md", "a\\b.md", "a//b.md", "a/./b.md", "a/../b.md"])
def test_manifest_refuses_unsafe_source_paths(source: str):
    with pytest.raises(ValueError, match="unsafe relative POSIX source"):
        state.manifest_sha256([_entry(source, "x")])


def test_manifest_refuses_duplicate_keys_and_inconsistent_source_hashes():
    a = _entry("notes/a.md", "one", "whole-v1")
    with pytest.raises(ValueError, match="duplicate manifest entry"):
        state.manifest_sha256([a, a])
    b = _entry("notes/a.md", "two", "whole-v2", chunk=1)
    with pytest.raises(ValueError, match="same source_sha256"):
        state.manifest_sha256([a, b])


def test_uncommitted_snapshot_is_not_marked_as_authoritative(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    snap = state.begin_snapshot("live_test_state", [_entry("a.md", "a")])
    assert state.active_snapshot("live_test_state") is None
    assert not state.tenant_is_managed("live_test_state")
    state.abort_snapshot(snap["snapshot_id"], "live_test_state")
    assert not state.tenant_is_managed("live_test_state")


def test_batch_requires_exact_manifest_and_records_only_after_claim(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    entry = _entry("a.md", "expected", "original file")
    snap = state.begin_snapshot("live_test_state", [entry])
    wrong = _doc(entry, "different bytes")
    with pytest.raises(ValueError, match="does not match manifest"):
        state.claim_snapshot_batch(snap["snapshot_id"], "live_test_state", [wrong])
    correct = _doc(entry, "expected")
    claimed = state.claim_snapshot_batch(snap["snapshot_id"], "live_test_state", [correct])
    assert claimed == [correct]
    with pytest.raises(ValueError, match="already in flight"):
        state.claim_snapshot_batch(snap["snapshot_id"], "live_test_state", [correct])
    with pytest.raises(ValueError, match="incomplete snapshot"):
        state.commit_snapshot(snap["snapshot_id"], "live_test_state", 1)
    state.record_uploaded(snap["snapshot_id"], "live_test_state", claimed)
    assert state.claim_snapshot_batch(snap["snapshot_id"], "live_test_state", [correct]) == []
    active, old = state.commit_snapshot(snap["snapshot_id"], "live_test_state", 1)
    assert active["expected_points"] == 1
    assert old is None
    assert state.active_snapshot("live_test_state")["snapshot_id"] == snap["snapshot_id"]


def test_activation_is_atomic_and_stale_builder_cannot_replace_newer_generation(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    first_entry = _entry("a.md", "revision one", "whole one")
    first, old = _publish("live_test_state", [first_entry], [_doc(first_entry, "revision one")])
    assert old is None

    stale_entry = _entry("a.md", "stale candidate", "whole stale")
    winner_entry = _entry("a.md", "winner", "whole winner")
    stale = state.begin_snapshot("live_test_state", [stale_entry])
    winner, previous = _publish("live_test_state", [winner_entry], [_doc(winner_entry, "winner")])
    assert previous == first["collection_name"]

    claimed = state.claim_snapshot_batch(stale["snapshot_id"], "live_test_state", [_doc(stale_entry, "stale candidate")])
    state.record_uploaded(stale["snapshot_id"], "live_test_state", claimed)
    with pytest.raises(ValueError, match="base is stale"):
        state.commit_snapshot(stale["snapshot_id"], "live_test_state", 1)
    assert state.active_snapshot("live_test_state")["snapshot_id"] == winner["snapshot_id"]


def test_empty_manifest_and_noncontiguous_chunks_are_rejected(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="empty source manifest"):
        state.begin_snapshot("live_test_state", [])
    entry = _entry("a.md", "chunk two", "whole source", chunk=2)
    with pytest.raises(ValueError, match="contiguous from zero"):
        state.begin_snapshot("live_test_state", [entry])


def test_verified_snapshot_persists_source_root_and_active_lookup(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    source_root = str(tmp_path / "source")
    entry = _entry("a.md", "chunk", "whole")
    snap = state.begin_snapshot("live_test_state", [entry], verify_sources=True, source_root=source_root)
    claimed = state.claim_snapshot_batch(snap["snapshot_id"], "live_test_state", [_doc(entry, "chunk")])
    state.record_uploaded(snap["snapshot_id"], "live_test_state", claimed)
    state.commit_snapshot(snap["snapshot_id"], "live_test_state", 1)
    active = state.active_snapshot("live_test_state")
    assert active["verify_sources"] == 1
    assert active["source_root"] == source_root
    assert state.tenant_is_managed("live_test_state")


def test_abort_is_idempotent_for_cleanup_retry(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    entry = _entry("a.md", "chunk")
    snap = state.begin_snapshot("live_test_state", [entry])
    first = state.abort_snapshot(snap["snapshot_id"], "live_test_state")
    second = state.abort_snapshot(snap["snapshot_id"], "live_test_state")
    assert first["status"] == "building"
    assert second["status"] == "aborted"


def test_search_hit_must_match_active_manifest_and_both_hashes(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    entry = _entry("notes/a.md", "retrieved exact text", "complete source file")
    active, _ = _publish("live_test_state", [entry], [_doc(entry, "retrieved exact text")])
    good = {"source": "notes/a.md", "chunk": 0, "text": "retrieved exact text",
            "metadata": {"_rag_content_sha256": entry["content_sha256"],
                         "_rag_source_sha256": entry["source_sha256"],
                         "_rag_snapshot_id": active["snapshot_id"]}}
    state.validate_snapshot_hits(active["snapshot_id"], "live_test_state", [good])
    tampered = {**good, "text": "tampered but still retrieved"}
    with pytest.raises(ValueError, match="does not match active manifest"):
        state.validate_snapshot_hits(active["snapshot_id"], "live_test_state", [tampered])
    wrong_provenance = {**good, "metadata": {**good["metadata"], "_rag_source_sha256": "0" * 64}}
    with pytest.raises(ValueError, match="hash/provenance metadata mismatch"):
        state.validate_snapshot_hits(active["snapshot_id"], "live_test_state", [wrong_provenance])


def test_failed_empty_or_partial_generation_does_not_replace_current(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    original_entry = _entry("keep.md", "original", "source original")
    original, _ = _publish("live_test_state", [original_entry], [_doc(original_entry, "original")])
    candidate_entry = _entry("new.md", "new", "source new")
    candidate = state.begin_snapshot("live_test_state", [candidate_entry])
    with pytest.raises(ValueError, match="incomplete snapshot"):
        state.commit_snapshot(candidate["snapshot_id"], "live_test_state", 0)
    assert state.active_snapshot("live_test_state")["snapshot_id"] == original["snapshot_id"]
