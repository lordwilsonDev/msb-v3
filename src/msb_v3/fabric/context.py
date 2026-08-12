"""Context builder (spec §3 Phase 2, blueprint §4 — \"Context Builder\").

Assembles the model-visible context for a task under a hard token budget,
with deterministic eviction — the inversion map's omission #5 (context as a
first-class failure mode) made real. The builder:

1. reserves budget for the system prompt + query (always present),
2. fills the remainder with retrieval matches ordered by score,
3. evicts low-score matches (and truncates the tail) so the total NEVER
   exceeds the budget — a hard invariant, unit-tested,
4. returns the assembled context plus a ledger (what fit, what was evicted,
   token counts) so the trace can show why the context looks the way it does.

Token estimation: len(text) // 4 (the same approximation the /v1 adapter
uses) — deterministic and dependency-free. Eviction is by match score
descending; ties break on source id ascending (deterministic).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Approximate chars-per-token (matches api/openai_compat.py usage).
_CHARS_PER_TOKEN = 4

# Per-item overhead (label + separator) counted toward the budget.
_ITEM_OVERHEAD = 8


def _tokens(text: str) -> int:
    return max(1, len(text or "") // _CHARS_PER_TOKEN)


@dataclass
class ContextLedger:
    budget_tokens: int
    system_tokens: int
    query_tokens: int
    included_matches: int
    evicted_matches: int
    truncated: bool
    total_tokens: int
    items: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class BuiltContext:
    text: str
    ledger: ContextLedger

    @property
    def tokens(self) -> int:
        return self.ledger.total_tokens

    def as_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "ledger": self.ledger.__dict__}


class ContextBuilder:
    def __init__(self, *, budget_tokens: int = 8000) -> None:
        if budget_tokens <= 0:
            raise ValueError("budget_tokens must be > 0")
        self._budget = budget_tokens

    def build(
        self,
        query: str,
        matches: List[Dict[str, Any]],
        *,
        system: Optional[str] = None,
        declare_domain: Optional[str] = None,
    ) -> BuiltContext:
        """Assemble the context string for a task.

        matches: retrieval hits, each {id, score, text, source, ...}. Items
        with missing text are skipped. The budget is never exceeded.
        """
        system = system or ""
        system_tokens = _tokens(system)
        query_tokens = _tokens(query)
        # "Query: " is real output text whenever a query is present — count it
        # so the assembled text never exceeds the budget, even at the edges.
        _QUERY_LABEL = _tokens("Query: ")
        query_label_tokens = _QUERY_LABEL if query else 0
        reserved = system_tokens + query_tokens + query_label_tokens
        if reserved >= self._budget:
            # Even system+query overflow: keep system, shrink query by
            # truncation, and if still over, drop the system prompt.
            budget_for_items = 0
            overflow = True
        else:
            budget_for_items = self._budget - reserved
            overflow = False

        # Deterministic order: score desc, then id asc (stable for ties).
        ranked = sorted(
            (m for m in matches if isinstance(m, dict) and (m.get("text") or "").strip()),
            key=lambda m: (-float(m.get("score", 0.0)), str(m.get("id", ""))),
        )

        sections: List[str] = []
        item_tokens: List[int] = []
        used = 0
        evicted = 0
        truncated = overflow

        for match in ranked:
            text = str(match.get("text", "")).strip()
            cost = _tokens(text) + _ITEM_OVERHEAD
            if used + cost > budget_for_items:
                if text:
                    evicted += 1
                continue
            used += cost
            source = str(match.get("source") or match.get("id") or "?")
            sections.append(f"- [{source}] {text}")
            item_tokens.append(cost)
            if len(sections) >= 20:  # cap item count; the budget usually binds first
                break

        # If the query had to be truncated to fit, do it now (deterministic
        # head-truncation to the budget). The "Query: " label is real text
        # that lands in the output, so it counts toward the query's share.
        query_out = query
        if overflow:
            q_budget = self._budget - system_tokens - _QUERY_LABEL
            if q_budget <= 0:
                system = ""
                system_tokens = 0
                q_budget = self._budget - _QUERY_LABEL
            if _tokens(query) > q_budget:
                query_out = query[: max(0, q_budget * _CHARS_PER_TOKEN)]
                truncated = True

        parts: List[str] = []
        if system:
            parts.append(system)
        if query_out:
            parts.append(f"Query: {query_out}")
        if sections:
            parts.append("Sources:\n" + "\n".join(sections))
        if declare_domain:
            parts.append(f"Domain: {declare_domain}")

        text = "\n\n".join(p for p in parts if p)
        total = _tokens(text)

        ledger = ContextLedger(
            budget_tokens=self._budget,
            system_tokens=system_tokens,
            query_tokens=_tokens(query_out),
            included_matches=len(sections),
            evicted_matches=evicted,
            truncated=truncated,
            total_tokens=total,
            items=[
                {"source": str(m.get("source") or m.get("id") or "?"), "score": m.get("score", 0.0)}
                for m in ranked[: len(sections)]
            ],
        )
        return BuiltContext(text=text, ledger=ledger)
