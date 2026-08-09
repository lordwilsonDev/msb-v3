from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from personal_intelligence.context_engine import ContextEngine
from personal_intelligence.memory_graph import MemoryGraph
from personal_intelligence.skill_engine import SkillEngine


@dataclass
class AgentBlueprint:
    name: str
    role: str
    skills: List[str]
    context_refs: List[str]
    graph_refs: List[str]


class AgentFactory:
    def __init__(
        self,
        context_engine: Optional[ContextEngine] = None,
        memory_graph: Optional[MemoryGraph] = None,
        skill_engine: Optional[SkillEngine] = None,
    ) -> None:
        self.context_engine = context_engine or ContextEngine()
        self.memory_graph = memory_graph or MemoryGraph()
        self.skill_engine = skill_engine or SkillEngine()

    def build_blueprint(self, name: str, role: str, query: str) -> AgentBlueprint:
        matched = self.skill_engine.match(query)
        context_refs = [chunk.source for chunk in self.context_engine.search(query)]
        graph_refs = [entity.id for entity in self.memory_graph.entities.values()]
        return AgentBlueprint(
            name=name,
            role=role,
            skills=[skill.name for skill in matched],
            context_refs=context_refs[:10],
            graph_refs=graph_refs[:20],
        )
