"""Tests for Stage 0 — Knowledge Acquisition Engine.

Uses NullResearchBackend throughout — no live network calls, matching the
2026-08-02 decision that CI must not depend on a real Tavily key.
"""
from __future__ import annotations

import importlib

import pytest

import msb_v3.triumvirate.mission_anchor as mission_anchor_mod
from msb_v3.uac.audit_chain import AuditChain
from msb_v3.uac.axiom_library import AxiomLibrary
from msb_v3.uac.models import RequirementsSpecification
from msb_v3.uac.observer_log import ObserverLog
from msb_v3.uac.research_backend import (
    NullResearchBackend,
    ResearchBackendError,
    SearchResult,
)
from msb_v3.uac.stage_0_knowledge_acquisition import (
    _RESEARCH_CATEGORIES,
    Stage0KnowledgeAcquisitionEngine,
)


def _isolated_mission_anchor(tmp_path, monkeypatch):
    mod = importlib.reload(mission_anchor_mod)
    monkeypatch.setattr(mod, "_RUNTIME_ROOT", tmp_path / "triumvirate")
    monkeypatch.setattr(mod, "_STATUS_FILE", tmp_path / "triumvirate" / "STATUS.json")
    return mod.MissionAnchor()


def _engine(tmp_path, monkeypatch, backend=None):
    backend = backend or NullResearchBackend(
        canned_results=[SearchResult(title="Test claim", url="https://example.gov/source", content="...", score=0.8)]
    )
    return Stage0KnowledgeAcquisitionEngine(
        research_backend=backend,
        mission_anchor=_isolated_mission_anchor(tmp_path, monkeypatch),
        observer_log=ObserverLog(db_path=str(tmp_path / "observer.db")),
        axiom_library=AxiomLibrary(db_path=str(tmp_path / "axiom.db")),
        audit_chain=AuditChain(db_path=str(tmp_path / "audit.db")),
    )


def test_validate_inputs_rejects_empty_profession(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    reqs = RequirementsSpecification(profession="", jurisdiction="Minnesota, USA")
    with pytest.raises(ValueError, match="profession"):
        engine.compile(reqs)


def test_validate_inputs_rejects_empty_jurisdiction(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    reqs = RequirementsSpecification(profession="bookkeeper", jurisdiction="")
    with pytest.raises(ValueError, match="jurisdiction"):
        engine.compile(reqs)


def test_compile_produces_manifest_with_all_categories(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    reqs = RequirementsSpecification(profession="bookkeeper", jurisdiction="Minnesota, USA")
    manifest = engine.compile(reqs)

    assert manifest.header.stage == "stage_0_knowledge_acquisition"
    assert manifest.outputs.primary_deliverable.profession == "bookkeeper"
    assert manifest.outputs.primary_deliverable.jurisdiction == "Minnesota, USA"
    category_names = {c.name for c in manifest.outputs.primary_deliverable.knowledge_categories}
    assert category_names == set(_RESEARCH_CATEGORIES.keys())


def test_compile_every_finding_has_a_source(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    reqs = RequirementsSpecification(profession="bookkeeper", jurisdiction="Minnesota, USA")
    manifest = engine.compile(reqs)

    for evidence in manifest.inputs.evidence:
        assert evidence.source  # "never guess" — every claim must carry a source


def test_compile_records_confidence_matrix(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    reqs = RequirementsSpecification(profession="bookkeeper", jurisdiction="Minnesota, USA")
    manifest = engine.compile(reqs)

    matrix = manifest.outputs.primary_deliverable.confidence_matrix
    assert set(matrix.keys()) == set(_RESEARCH_CATEGORIES.keys())
    assert all(0.0 <= v <= 1.0 for v in matrix.values())


def test_compile_with_no_search_results_populates_known_unknowns(tmp_path, monkeypatch):
    empty_backend = NullResearchBackend(canned_results=[])
    engine = _engine(tmp_path, monkeypatch, backend=empty_backend)
    reqs = RequirementsSpecification(profession="bookkeeper", jurisdiction="Minnesota, USA")
    manifest = engine.compile(reqs)

    assert len(manifest.outputs.primary_deliverable.known_unknowns) == len(_RESEARCH_CATEGORIES)
    assert len(manifest.failure_report.missing_information) == len(_RESEARCH_CATEGORIES)


class _FailingBackend:
    def search(self, query: str, max_results: int = 5):
        raise ResearchBackendError("simulated backend outage")


def test_compile_handles_backend_failure_without_crashing(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch, backend=_FailingBackend())
    reqs = RequirementsSpecification(profession="bookkeeper", jurisdiction="Minnesota, USA")
    manifest = engine.compile(reqs)

    assert len(manifest.failure_report.research_limitations) == len(_RESEARCH_CATEGORIES)
    for category in manifest.outputs.primary_deliverable.knowledge_categories:
        assert category.findings == []


def test_compile_publishes_to_axiom_library(tmp_path, monkeypatch):
    lib = AxiomLibrary(db_path=str(tmp_path / "axiom.db"))
    engine = Stage0KnowledgeAcquisitionEngine(
        research_backend=NullResearchBackend(canned_results=[SearchResult(title="x", url="https://example.gov", content="", score=0.9)]),
        mission_anchor=_isolated_mission_anchor(tmp_path, monkeypatch),
        observer_log=ObserverLog(db_path=str(tmp_path / "observer.db")),
        axiom_library=lib,
        audit_chain=AuditChain(db_path=str(tmp_path / "audit.db")),
    )
    reqs = RequirementsSpecification(profession="bookkeeper", jurisdiction="Minnesota, USA")
    manifest = engine.compile(reqs)

    stored = lib.get(manifest.header.artifact_id)
    assert stored is not None
    assert stored.profession == "bookkeeper"


def test_compile_writes_audit_chain_events(tmp_path, monkeypatch):
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    engine = Stage0KnowledgeAcquisitionEngine(
        research_backend=NullResearchBackend(canned_results=[]),
        mission_anchor=_isolated_mission_anchor(tmp_path, monkeypatch),
        observer_log=ObserverLog(db_path=str(tmp_path / "observer.db")),
        axiom_library=AxiomLibrary(db_path=str(tmp_path / "axiom.db")),
        audit_chain=chain,
    )
    reqs = RequirementsSpecification(profession="bookkeeper", jurisdiction="Minnesota, USA")
    engine.compile(reqs)

    events = chain.get_chain(component="stage_0_knowledge_acquisition")
    event_types = [e.event_type for e in events]
    assert "mission_started" in event_types
    assert "manifest_published" in event_types
    assert chain.verify_chain()["valid"] is True


def test_compile_narrates_progress(tmp_path, monkeypatch):
    log = ObserverLog(db_path=str(tmp_path / "observer.db"))
    anchor = _isolated_mission_anchor(tmp_path, monkeypatch)
    engine = Stage0KnowledgeAcquisitionEngine(
        research_backend=NullResearchBackend(canned_results=[]),
        mission_anchor=anchor,
        observer_log=log,
        axiom_library=AxiomLibrary(db_path=str(tmp_path / "axiom.db")),
        audit_chain=AuditChain(db_path=str(tmp_path / "audit.db")),
    )
    reqs = RequirementsSpecification(profession="bookkeeper", jurisdiction="Minnesota, USA")
    engine.compile(reqs)

    mission_id = anchor.read()["scope_hash"]
    narration = log.read_as_text(mission_id)
    assert "Research started." in narration
    assert "Manifest generated." in narration
