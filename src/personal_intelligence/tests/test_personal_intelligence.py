"""Tests for Personal Intelligence Layer (PIL).

Validates:
- context engine ingestion/retrieval/search
- memory graph entity/relation/neighbor lookups
- skill engine parsing/matching/directory loading
- agent factory blueprint generation
- provenance ledger promotion/summary
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personal_intelligence.agent_factory import AgentFactory
from personal_intelligence.context_engine import (
    ContextChunk,
    ContextEngine,
)
from personal_intelligence.memory_graph import (
    Entity,
    MemoryGraph,
    Relationship,
)
from personal_intelligence.provenance import (
    MemoryLedger,
    ProvenanceEntry,
)
from personal_intelligence.skill_engine import SkillEngine


def test_context_engine_ingest_and_search() -> None:
    engine = ContextEngine()
    idx = engine.ingest(ContextChunk(source="notes.md", content="Buy milk and bread"))
    assert idx == 0
    assert engine.retrieve("notes.md").content == "Buy milk and bread"
    assert engine.search("milk") == [engine.chunks[0]]
    assert engine.search("coffee") == []


def test_memory_graph_adds_and_neighbors() -> None:
    graph = MemoryGraph()
    graph.add_entity(Entity(id="wilson", type="person"))
    graph.add_entity(Entity(id="msb", type="project"))
    graph.add_relation(Relationship(source_id="wilson", target_id="msb", relation="owns"))
    neighbors = graph.neighbors("wilson")
    assert [n.id for n in neighbors] == ["msb"]


def test_skill_engine_parses_frontmatter() -> None:
    engine = SkillEngine()
    skill = engine._split_frontmatter("---\ntriggers: deploy,ship\n---\nbody")[0]
    assert skill["triggers"] == "deploy,ship"


def test_skill_engine_matches_by_triggers() -> None:
    engine = SkillEngine()
    engine.register(
        SkillEngine._parse_fragment(  # type: ignore[attr-defined]
            "deploy-skill",
            Path("/tmp"),
            "---\ntriggers: deploy,ship\n---\nDo deploy.",
        )
    )
    hits = engine.match("please deploy this")
    assert [skill.name for skill in hits] == ["deploy-skill"]


def test_memory_ledger_promotes_entries() -> None:
    ledger = MemoryLedger()
    ledger.record(
        ProvenanceEntry(key="fact-1", value="answer is 7", confidence=0.5)
    )
    ledger.promote("fact-1", confidence=0.95, source="mmvp-gate-2")
    assert ledger.entries["fact-1"].verified is True
    assert ledger.entries["fact-1"].confidence == 0.95
    assert ledger.summary()[0]["key"] == "fact-1"


def test_agent_factory_blueprint_from_skills() -> None:
    factory = AgentFactory(
        context_engine=ContextEngine(),
        memory_graph=MemoryGraph(),
        skill_engine=SkillEngine(),
    )
    factory.skill_engine.register(
        SkillEngine._parse_fragment(  # type: ignore[attr-defined]
            "meeting-skill",
            Path("/tmp"),
            "---\ntriggers: meeting,notes\n---\nProcess meetings.",
        )
    )
    blueprint = factory.build_blueprint(
        name="meeting-agent", role="processes meeting notes", query="meeting notes"
    )
    assert blueprint.name == "meeting-agent"
    assert "meeting-skill" in blueprint.skills


def test_context_engine_search_case_insensitive() -> None:
    engine = ContextEngine()
    engine.ingest(ContextChunk(source="vault", content="Deep Research"))
    assert [chunk.content for chunk in engine.search("deep")] == ["Deep Research"]
    assert [chunk.content for chunk in engine.search("RESEARCH")] == ["Deep Research"]


def test_memory_graph_ignores_missing_relation_type() -> None:
    graph = MemoryGraph()
    graph.add_entity(Entity(id="a", type="x"))
    graph.add_entity(Entity(id="b", type="y"))
    graph.add_relation(Relationship(source_id="a", target_id="b", relation="knows"))
    assert graph.neighbors("a", relation="likes") == []


def test_provenance_entry_defaults() -> None:
    entry = ProvenanceEntry(key="k", value=1)
    assert entry.verified is False
    assert entry.confidence == 0.0
    assert entry.source == "unknown"
    assert entry.recorded_at >= "2026-"
