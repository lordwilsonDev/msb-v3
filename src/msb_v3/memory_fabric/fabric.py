"""Memory Fabric operations (spec §4.2.2).

    store_memory(...)    persist a memory with provenance
    recall_memories(...) score + rank memories for a query
    verify_memory(...)   transition verification state (audited)
    forget_memory(...)   soft delete (archived + DEPRECATED record)
    consolidate(...)     merge near-duplicate memories + decay everything

Recall scoring: keyword hits (content/tag overlap) are the primary,
deterministic path; a semantic boost is added when the query embedding
search returns candidates. Final rank = hit score × live_score
(importance × recency — spec §17). Touching a memory on recall updates
its last_accessed_at so recency is honest.

Consolidation: pairs of memories sharing project+type and ≥1 tag, or
sharing project+type with high tag overlap, are merged — the higher-
importance one survives with combined content/tags, the merged-away one
is DEPRECATED with a reason pointing at the survivor. Decay is applied
to every active memory (importance scaled by recency, floored).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from msb_v3.memory_fabric.models import (
    VERIFICATION_TRANSITIONS,
    MemoryItem,
    MemoryType,
    VerificationState,
)
from msb_v3.memory_fabric.store import MemoryFabricStore


@dataclass
class MemoryRecall:
    memory_id: str
    content: str
    type: str
    tags: List[str]
    importance: float
    verification_state: str
    score: float
    source_agent: str
    project: str
    tech: str

    def as_dict(self) -> Dict[str, Any]:
        return self.__dict__


class MemoryFabric:
    def __init__(self, store: MemoryFabricStore) -> None:
        self.store = store

    # -- write ------------------------------------------------------------

    def store_memory(
        self,
        content: str,
        *,
        type_: MemoryType = MemoryType.SEMANTIC,
        tags: Optional[List[str]] = None,
        importance: float = 0.5,
        source_agent: str = "",
        source: str = "",
        task_id: str = "",
        tenant: str = "default",
        project: str = "",
        tech: str = "",
        decay_factor: float = 0.9,
        relationships: Optional[List[str]] = None,
        memory_id: Optional[str] = None,
    ) -> MemoryItem:
        if not content or not content.strip():
            raise ValueError("content is required")
        importance = max(0.0, min(1.0, importance))
        item = MemoryItem(
            memory_id=memory_id or uuid.uuid4().hex[:16],
            type=type_,
            content=content.strip(),
            tags=tags or [],
            importance=importance,
            source_agent=source_agent,
            source=source,
            task_id=task_id,
            tenant=tenant,
            project=project,
            tech=tech,
            verification_state=VerificationState.UNVERIFIED,
            decay_factor=decay_factor,
            relationships=relationships or [],
        )
        self.store.upsert(item)
        return item

    # -- read -------------------------------------------------------------

    def recall_memories(
        self,
        query: str,
        *,
        tenant: str = "default",
        project: Optional[str] = None,
        tech: Optional[str] = None,
        type_: Optional[MemoryType] = None,
        top_k: int = 8,
        semantic: bool = True,
    ) -> List[MemoryRecall]:
        """Rank memories for a query.

        Primary: literal keyword matches over content/tags (deterministic).
        Boost: when ``semantic`` and the embedding seam is configured,
        candidate ids from Qdrant are scored higher. Every recalled memory
        is touched (recency is honest).
        """
        candidates: Dict[str, MemoryItem] = {}
        for item in self.store.search_keywords(tenant, query, limit=50):
            candidates[item.memory_id] = item

        semantic_hits: set[str] = set()
        if semantic:
            for item in self.store.search_embedding(tenant, query, top_k=top_k * 3):
                candidates.setdefault(item.memory_id, item)
                semantic_hits.add(item.memory_id)

        # Apply project/tech/type filters (post-filter: keyword path has no
        # column filter; the query should still respect them).
        filtered = []
        for item in candidates.values():
            if project and item.project != project:
                continue
            if tech and item.tech != tech:
                continue
            if type_ and item.type != type_:
                continue
            filtered.append(item)

        now = time.time()
        ranked = []
        for item in filtered:
            hit_score = self._keyword_score(item, query)
            sem = 1.5 if item.memory_id in semantic_hits else 1.0
            score = round(hit_score * item.live_score(now) * sem, 4)
            ranked.append((score, item))

        ranked.sort(key=lambda pair: pair[0], reverse=True)
        results = []
        for score, item in ranked[:top_k]:
            self.store.touch(item.memory_id)
            results.append(
                MemoryRecall(
                    memory_id=item.memory_id,
                    content=item.content,
                    type=item.type.value,
                    tags=list(item.tags),
                    importance=item.importance,
                    verification_state=item.verification_state.value,
                    score=score,
                    source_agent=item.source_agent,
                    project=item.project,
                    tech=item.tech,
                )
            )
        return results

    @staticmethod
    def _keyword_score(item: MemoryItem, query: str) -> float:
        """Deterministic content/tag overlap score (0..1+)."""
        q_terms = [t for t in query.lower().split() if len(t) >= 2]
        if not q_terms:
            return 0.0
        content = item.content.lower()
        tag_blob = " ".join(item.tags).lower()
        hits = sum(1 for t in q_terms if t in content or t in tag_blob)
        return hits / len(q_terms)

    # -- verification -------------------------------------------------------

    def verify_memory(
        self,
        memory_id: str,
        to_state: VerificationState,
        *,
        by: str = "operator",
        reason: str = "",
    ) -> MemoryItem:
        """Transition a memory's verification state. Every transition is
        recorded (audit trail) and only legal transitions are allowed —
        fail-closed."""
        item = self.store.get(memory_id)
        if item is None:
            raise KeyError(f"unknown memory: {memory_id}")
        if item.archived:
            raise ValueError(f"memory {memory_id} is archived (DEPRECATED is terminal)")
        allowed = VERIFICATION_TRANSITIONS.get(item.verification_state.value, ())
        if to_state.value not in allowed:
            raise ValueError(
                f"illegal verification transition: {item.verification_state.value} -> {to_state.value}"
            )
        from_state = item.verification_state.value
        item.verification_state = to_state
        item.updated_at = time.time()
        self.store.upsert(item)
        self.store.record_verification(memory_id, from_state, to_state.value, by=by, reason=reason)
        return item

    # -- forget / consolidate ------------------------------------------------

    def forget_memory(self, memory_id: str, *, by: str = "operator", reason: str = "forgotten") -> MemoryItem:
        """Soft delete: archive the row (provenance preserved) and record a
        DEPRECATED transition. DEPRECATED is terminal — no revival path."""
        item = self.store.get(memory_id)
        if item is None:
            raise KeyError(f"unknown memory: {memory_id}")
        if item.archived:
            return item
        from_state = item.verification_state.value
        item.archived = True
        item.verification_state = VerificationState.DEPRECATED
        item.updated_at = time.time()
        self.store.upsert(item)
        self.store.record_verification(memory_id, from_state, "DEPRECATED", by=by, reason=reason)
        return item

    def consolidate(self, tenant: str = "default", *, by: str = "system") -> Dict[str, Any]:
        """Merge near-duplicate active memories and decay every active one.

        Returns {merged, deprecations, decayed, kept} — honest counts so a
        caller can see exactly what changed.
        """
        items = self.store.list_active(tenant, limit=1000)
        merged = 0
        deprecations: List[str] = []
        decayed = 0
        kept_ids: set[str] = set()

        # Group candidates: same project + type + ≥1 shared tag.
        groups: Dict[tuple, List[MemoryItem]] = {}
        for item in items:
            key = (item.project, item.type.value)
            groups.setdefault(key, []).append(item)

        for key, group in groups.items():
            if len(group) < 2:
                kept_ids.add(group[0].memory_id)
                continue
            # Pairwise merge within the group (highest importance first).
            group.sort(key=lambda i: i.importance, reverse=True)
            consumed: set[str] = set()
            for i, item in enumerate(group):
                if item.memory_id in consumed:
                    continue
                survivor = item
                kept_ids.add(survivor.memory_id)
                for other in group[i + 1:]:
                    if other.memory_id in consumed:
                        continue
                    if not self._duplicate(survivor, other):
                        continue
                    # merge other into survivor
                    survivor.tags = list(dict.fromkeys(survivor.tags + other.tags))
                    survivor.relationships = list(
                        dict.fromkeys(survivor.relationships + [other.memory_id])
                    )
                    survivor.content = f"{survivor.content}\n\n{other.content}"
                    survivor.importance = max(survivor.importance, other.importance)
                    survivor.updated_at = time.time()
                    consumed.add(other.memory_id)
                    merged += 1
                    deprecations.append(other.memory_id)
                    self.forget_memory(other.memory_id, by=by, reason=f"consolidated into {survivor.memory_id}")
            for s in group:
                if s.memory_id not in consumed:
                    self.store.upsert(s)

        # Decay every surviving active memory: importance scaled by recency
        # (floored) — spec §17.
        now = time.time()
        for item in self.store.list_active(tenant, limit=1000):
            age = max(0.0, now - item.last_accessed_at)
            periods = age / (30 * 24 * 3600)
            factor = max(0.3, item.decay_factor ** periods)
            new_importance = round(item.importance * factor, 4)
            if new_importance < item.importance:
                item.importance = new_importance
                item.updated_at = now
                self.store.upsert(item)
                decayed += 1
            kept_ids.add(item.memory_id)

        return {
            "tenant": tenant,
            "merged": merged,
            "deprecations": deprecations,
            "decayed": decayed,
            "kept": len(kept_ids),
        }

    @staticmethod
    def _duplicate(a: MemoryItem, b: MemoryItem) -> bool:
        """Two memories are merge candidates when they share a project, a
        type, and at least one tag (or high content overlap)."""
        shared_tags = set(a.tags) & set(b.tags)
        if shared_tags:
            return True
        a_words = set(a.content.lower().split())
        b_words = set(b.content.lower().split())
        if not a_words or not b_words:
            return False
        overlap = len(a_words & b_words) / max(1, len(a_words | b_words))
        return overlap >= 0.5
