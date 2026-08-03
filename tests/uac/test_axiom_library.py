"""Tests for uac.axiom_library.AxiomLibrary."""
from __future__ import annotations

import pytest

from msb_v3.uac.axiom_library import ArtifactRecord, AxiomLibrary


def _lib(tmp_path) -> AxiomLibrary:
    return AxiomLibrary(db_path=str(tmp_path / "axiom.db"))


def test_publish_and_get(tmp_path):
    lib = _lib(tmp_path)
    record = ArtifactRecord(
        artifact_id="art-1", stage="stage_0", version="1.0.0",
        payload={"foo": "bar"}, profession="bookkeeper", jurisdiction="US",
    )
    published = lib.publish(record)
    assert published.created_at is not None

    fetched = lib.get("art-1")
    assert fetched is not None
    assert fetched.payload == {"foo": "bar"}
    assert fetched.profession == "bookkeeper"


def test_get_missing_returns_none(tmp_path):
    lib = _lib(tmp_path)
    assert lib.get("does-not-exist") is None


def test_publish_duplicate_version_raises(tmp_path):
    lib = _lib(tmp_path)
    record = ArtifactRecord(artifact_id="art-1", stage="stage_0", version="1.0.0", payload={})
    lib.publish(record)
    with pytest.raises(Exception):
        lib.publish(record)  # artifacts are immutable once published — same (id, version) must fail


def test_get_latest_version_when_unspecified(tmp_path):
    lib = _lib(tmp_path)
    lib.publish(ArtifactRecord(artifact_id="art-1", stage="stage_0", version="1.0.0", payload={"v": 1}))
    lib.publish(ArtifactRecord(artifact_id="art-1", stage="stage_0", version="1.0.1", payload={"v": 2}))
    latest = lib.get("art-1")
    assert latest.payload == {"v": 2}
    v1 = lib.get("art-1", version="1.0.0")
    assert v1.payload == {"v": 1}


def test_list_versions(tmp_path):
    lib = _lib(tmp_path)
    lib.publish(ArtifactRecord(artifact_id="art-1", stage="stage_0", version="1.0.0", payload={}))
    lib.publish(ArtifactRecord(artifact_id="art-1", stage="stage_0", version="1.0.1", payload={}))
    versions = lib.list_versions("art-1")
    assert [v.version for v in versions] == ["1.0.0", "1.0.1"]


def test_list_by_stage_and_profession(tmp_path):
    lib = _lib(tmp_path)
    lib.publish(ArtifactRecord(artifact_id="a", stage="stage_0", version="1.0.0", payload={}, profession="bookkeeper"))
    lib.publish(ArtifactRecord(artifact_id="b", stage="stage_1", version="1.0.0", payload={}, profession="bookkeeper"))
    lib.publish(ArtifactRecord(artifact_id="c", stage="stage_0", version="1.0.0", payload={}, profession="electrician"))

    by_stage = lib.list_by_stage("stage_0")
    assert {r.artifact_id for r in by_stage} == {"a", "c"}

    by_profession = lib.list_by_profession("bookkeeper")
    assert {r.artifact_id for r in by_profession} == {"a", "b"}
