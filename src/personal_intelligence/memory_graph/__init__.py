from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class Entity:
    id: str
    type: str
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Relationship:
    source_id: str
    target_id: str
    relation: str
    since: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MemoryGraph:
    def __init__(self) -> None:
        self.entities: Dict[str, Entity] = {}
        self.relations: List[Relationship] = []

    def add_entity(self, entity: Entity) -> None:
        self.entities[entity.id] = entity

    def add_relation(self, relation: Relationship) -> None:
        self.relations.append(relation)

    def neighbors(self, entity_id: str, relation: Optional[str] = None) -> List[Entity]:
        out: List[Entity] = []
        for rel in self.relations:
            if relation is not None and rel.relation != relation:
                continue
            target_id = rel.target_id if rel.source_id == entity_id else rel.source_id
            if target_id in self.entities:
                out.append(self.entities[target_id])
        return out
