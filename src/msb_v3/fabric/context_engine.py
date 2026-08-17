"""Context Engine (Sovereign Architecture v4.0 §4.2.3, P1).

Curates and compresses the context handed to an agent, by layer:

    L0  system invariants        always present (version, host, tenant…)
    L1  task description         always present (the task itself)
    L2  repository structure     from the Code Graph (find_symbol)
    L3  relevant code            surgical snippets from the Code Graph
                                 (context_of — signatures + locations, never
                                 whole files)
    L4  relevant memories        from the Memory Fabric (recall_memories)
    L5  relevant skills          from the skill registry (description match)
    L6  historical context       from the AuditChain (recent task events)
    L7  external research        pluggable slot; OFF by default (the
                                 research engine is a heavy async run — an
                                 engine must opt in by injecting a retriever)

Every retriever is best-effort and never raises: a failed seam (Qdrant
down, repo not indexed, no skills installed) contributes an empty layer
with a reason in the ledger — the engine degrades honestly, never fakes
content. Retrievers are injectable so tests pin composition semantics
without touching real seams.

Budget model (the spec's hard invariant): each layer has a per-layer cap;
the assembled context has a hard TOTAL cap. Required layers (L0/L1) always
fit (they are tiny); optional layers are included in priority order and
evicted bottom-up when the total would overflow. The ledger reports every
layer's requested vs included tokens plus what was evicted/truncated, so
the trace can show exactly why the context looks the way it does.

The naive baseline (all matches, no budget) is recorded on every compose,
so validation gate G3 (≥50% token reduction vs naive) is measurable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4  # matches the /v1 adapter and fabric/context.py

_NAIVE_CONTEXT_MAX = 64_000  # chars — a rough "dump everything" baseline


class _Default:
    """Sentinel: 'use the built-in retriever for this layer'. A distinct
    object (not a string) so literal string overrides never collide."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


_DEFAULT = _Default()


def _tokens(text: str) -> int:
    return max(1, len(text or "") // _CHARS_PER_TOKEN)


@dataclass
class LayerResult:
    layer_id: str  # "L0" .. "L7"
    name: str
    text: str
    requested_tokens: int = 0
    included_tokens: int = 0
    evicted: bool = False
    reason: str = ""  # why this layer is empty/evicted (honest ledger)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "layer": self.layer_id,
            "name": self.name,
            "requested_tokens": self.requested_tokens,
            "included_tokens": self.included_tokens,
            "evicted": self.evicted,
            "reason": self.reason,
        }


@dataclass
class ContextPackage:
    text: str
    budget_tokens: int
    total_tokens: int
    naive_tokens: int  # the un-budgeted baseline (G3 measurement)
    layers: List[LayerResult] = field(default_factory=list)

    @property
    def reduction_pct(self) -> float:
        """Token reduction vs the naive baseline (validation gate G3)."""
        if self.naive_tokens <= 0:
            return 0.0
        return round(max(0.0, 1.0 - self.total_tokens / self.naive_tokens) * 100, 1)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "budget_tokens": self.budget_tokens,
            "total_tokens": self.total_tokens,
            "naive_tokens": self.naive_tokens,
            "reduction_pct": self.reduction_pct,
            "layers": [layer.as_dict() for layer in self.layers],
        }


class ContextEngine:
    """Compose a layered, budgeted context for a task.

    ``retrievers`` maps layer id -> callable returning a text string (or
    (text, reason)). Missing/None entries use the built-in default
    retriever for that layer (all best-effort). Passing ``{layer_id:
    callable}`` overrides; passing ``{layer_id: None}`` disables a layer.
    """

    LAYER_NAMES = {
        "L0": "system-invariants",
        "L1": "task",
        "L2": "repository-structure",
        "L3": "relevant-code",
        "L4": "memories",
        "L5": "skills",
        "L6": "history",
        "L7": "research",
    }
    REQUIRED = {"L0", "L1"}
    # optional layers, highest priority first (L2 > L3 > …)
    OPTIONAL_ORDER = ("L2", "L3", "L4", "L5", "L6", "L7")

    def __init__(
        self,
        *,
        retrievers: Optional[Dict[str, Callable[[], Any]]] = None,
        default_budget: int = 4000,
    ) -> None:
        self._overrides = retrievers or {}
        self.default_budget = default_budget

    # -- public ----------------------------------------------------------

    def compose(
        self,
        task: str,
        *,
        tenant: str = "default",
        session: str = "default",
        repo: Optional[str] = None,
        project: Optional[str] = None,
        tech: Optional[str] = None,
        budget_tokens: Optional[int] = None,
    ) -> ContextPackage:
        """Assemble the layered context for one task.

        Every layer runs (best-effort), each is capped at its per-layer
        budget, then layers assemble in priority order under the hard
        total. The naive baseline (un-budgeted concatenation of everything
        the retrievers produced) is measured for gate G3.
        """
        budget = budget_tokens or self.default_budget
        ctx: Dict[str, str] = {}
        reasons: Dict[str, str] = {}
        for layer_id in ("L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7"):
            text, reason = self._retrieve(layer_id, task, tenant, session, repo, project, tech)
            ctx[layer_id] = text
            reasons[layer_id] = reason

        # Per-layer caps: L0/L1 are small and always fit; optional layers
        # get a share of the budget (roughly budget / 6 each, floored).
        per_layer = {lid: max(100, budget // 6) for lid in self.OPTIONAL_ORDER}

        # Naive baseline: everything, no budget (for G3).
        naive_text = "\n\n".join(t for t in ctx.values() if t)
        naive_tokens = _tokens(naive_text[:_NAIVE_CONTEXT_MAX])

        results: List[LayerResult] = []
        included: List[str] = []
        used = 0

        def _add(layer_id: str, text: str, cap: Optional[int], reason: str) -> None:
            nonlocal used
            req = _tokens(text) if text else 0
            if not text:
                results.append(LayerResult(layer_id, self.LAYER_NAMES[layer_id], "", reason=reason or "no content"))
                return
            body = text
            truncated = False
            if cap is not None and req > cap:
                body = body[: cap * _CHARS_PER_TOKEN]
                truncated = True
            tok = _tokens(body)
            if used + tok > budget and layer_id not in self.REQUIRED:
                results.append(
                    LayerResult(
                        layer_id, self.LAYER_NAMES[layer_id], "",
                        requested_tokens=req, evicted=True,
                        reason="evicted: hard total budget exceeded",
                    )
                )
                return
            if layer_id in self.REQUIRED and used + tok > budget:
                # Required layers always fit: shrink to the remaining room.
                room = max(0, budget - used)
                if room <= 0:
                    results.append(LayerResult(layer_id, self.LAYER_NAMES[layer_id], "", requested_tokens=req, evicted=True, reason="no room"))
                    return
                body = body[: room * _CHARS_PER_TOKEN]
                tok = _tokens(body)
            included.append(body)
            used += tok
            results.append(
                LayerResult(
                    layer_id, self.LAYER_NAMES[layer_id], body,
                    requested_tokens=req, included_tokens=tok,
                    reason=("truncated to per-layer cap" if truncated else reason or ""),
                )
            )

        # Required layers (L0/L1) first — each is guaranteed at least a
        # share of the budget so both ALWAYS fit, however squeezed. The
        # required share is half the total for two layers; optional layers
        # take whatever remains.
        required_share = budget // len(self.REQUIRED) if self.REQUIRED else budget
        _add("L0", ctx["L0"], required_share, reasons["L0"])
        _add("L1", ctx["L1"], required_share, reasons["L1"])
        for lid in self.OPTIONAL_ORDER:
            if not ctx.get(lid):
                results.append(
                    LayerResult(lid, self.LAYER_NAMES[lid], "", reason=reasons.get(lid) or "no content")
                )
                continue
            _add(lid, ctx[lid], per_layer[lid], reasons[lid])

        text = "\n\n".join(p for p in included if p)
        total = _tokens(text)
        return ContextPackage(
            text=text,
            budget_tokens=budget,
            total_tokens=total,
            naive_tokens=naive_tokens,
            layers=results,
        )

    # -- layer retrievers --------------------------------------------------

    def _retrieve(
        self, layer_id: str, task: str, tenant: str, session: str,
        repo: Optional[str], project: Optional[str], tech: Optional[str],
    ) -> tuple[str, str]:
        override = self._overrides.get(layer_id, _DEFAULT)
        if override is None:
            return "", "disabled"
        if callable(override):
            try:
                out = override()
                if isinstance(out, tuple):
                    return out
                return str(out or ""), ""
            except Exception as exc:
                logger.debug("context layer %s retriever failed: %s", layer_id, exc)
                return "", f"retriever error: {type(exc).__name__}"
        if isinstance(override, str):
            # literal content override (tests pin composition semantics)
            return override, ""
        try:
            fn = getattr(self, f"_retrieve_{layer_id.lower()}")
            return fn(task, tenant, session, repo, project, tech)
        except Exception as exc:
            logger.debug("context layer %s failed: %s", layer_id, exc)
            return "", f"error: {type(exc).__name__}: {exc}"

    def _retrieve_l0(self, task, tenant, session, repo, project, tech) -> tuple[str, str]:
        from msb_v3 import __version__
        from msb_v3.core.config import settings

        return (
            "\n".join(
                [
                    f"System: msb-v3 v{__version__} (sovereign local-first runtime)",
                    f"Tenant: {tenant} | Session: {session}",
                    f"Host: {settings.host}",
                    "Governance: operator-gated APIs; governed tools only; no silent fallback.",
                ]
            ),
            "",
        )

    def _retrieve_l1(self, task, tenant, session, repo, project, tech) -> tuple[str, str]:
        return f"Task: {task}", ""

    def _retrieve_l2(self, task, tenant, session, repo, project, tech) -> tuple[str, str]:
        if not repo:
            return "", "no repo provided"
        from msb_v3.codegraph.queries import CodeGraphQueries
        from msb_v3.codegraph.store import CodeGraphStore
        from msb_v3.core.config import settings

        symbols = self._task_symbols(task)
        if not symbols:
            return "", "no symbols extracted from task"
        queries = CodeGraphQueries(CodeGraphStore(settings.codegraph_db_path))
        lines = []
        seen: set[str] = set()
        for sym in symbols[:4]:
            for hit in queries.find_symbol(repo, sym, limit=3):
                if hit["fq_name"] in seen:
                    continue
                seen.add(hit["fq_name"])
                approx = " ~" if hit.get("approximate") else ""
                lines.append(f"- [{hit['kind']}] {hit['fq_name']} @ {hit['file']}:{hit['line']}{approx}")
        if not lines:
            return "", f"repo {repo} not indexed or no matches"
        return "\n".join(lines), ""

    def _retrieve_l3(self, task, tenant, session, repo, project, tech) -> tuple[str, str]:
        if not repo:
            return "", "no repo provided"
        from msb_v3.codegraph.queries import CodeGraphQueries
        from msb_v3.codegraph.store import CodeGraphStore
        from msb_v3.core.config import settings

        symbols = self._task_symbols(task)
        if not symbols:
            return "", "no symbols extracted from task"
        queries = CodeGraphQueries(CodeGraphStore(settings.codegraph_db_path))
        lines = []
        seen: set[str] = set()
        for sym in symbols[:3]:
            ctx = queries.context_of(repo, sym)
            if not ctx.get("found"):
                continue
            head = f"{ctx['kind']} {ctx['symbol']} @ {ctx['file']}:{ctx['line']} {ctx.get('signature','')}"
            if head in seen:
                continue
            seen.add(head)
            lines.append(head)
            for c in (ctx.get("callers") or [])[:3]:
                lines.append(f"  caller: {c['symbol']} @ {c['file']}:{c['line']}")
            for c in (ctx.get("callees") or [])[:3]:
                lines.append(f"  callee: {c['symbol']} @ {c['file']}:{c['line']}")
        if not lines:
            return "", "no symbol context found"
        return "\n".join(lines), ""

    def _retrieve_l4(self, task, tenant, session, repo, project, tech) -> tuple[str, str]:
        from msb_v3.core.config import settings
        from msb_v3.memory_fabric.fabric import MemoryFabric
        from msb_v3.memory_fabric.store import MemoryFabricStore

        fabric = MemoryFabric(MemoryFabricStore(settings.memory_fabric_db_path))
        hits = fabric.recall_memories(task, tenant=tenant, project=project, tech=tech, top_k=5)
        if not hits:
            return "", "no memory matches"
        return "\n".join(
            f"- [{h.score:.2f}] ({h.type}/{h.verification_state}) {h.content[:200]}" for h in hits
        ), ""

    def _retrieve_l5(self, task, tenant, session, repo, project, tech) -> tuple[str, str]:
        try:
            from msb_v3.api.skill_router import _list_skills
        except Exception:
            return "", "skill registry unavailable"
        skills = _list_skills()
        if not skills:
            return "", "no skills installed"
        task_lower = task.lower()
        terms = [t for t in re.findall(r"[a-z][a-z0-9_-]{2,}", task_lower) if len(t) >= 3]
        matched = []
        for s in skills:
            blob = f"{s['name']} {s.get('description','')}".lower()
            if any(t in blob for t in terms[:8]):
                matched.append(f"- {s['name']} ({s['category']}): {s.get('description','')[:80]}")
        if not matched:
            return "", "no skills matched the task"
        return "\n".join(matched[:5]), ""

    def _retrieve_l6(self, task, tenant, session, repo, project, tech) -> tuple[str, str]:
        try:
            from msb_ledger.audit_chain import AuditChain
            from msb_v3.core.config import settings as cfg
        except Exception:
            return "", "audit chain unavailable"
        chain = AuditChain(db_path=cfg.db_path, allow_keyless=True)
        try:
            records = chain.get_chain(component="tasks")
        except Exception:
            return "", "audit chain read failed"
        recent = records[-5:] if records else []
        if not recent:
            return "", "no task audit records yet"
        lines = []
        for r in recent:
            payload = getattr(r, "payload", None) or {}
            event = getattr(r, "event_type", "") or payload.get("event_type", "")
            ts = str(getattr(r, "timestamp", ""))[:19]
            lines.append(f"- {ts} {event}")
        return "\n".join(lines), ""

    def _retrieve_l7(self, task, tenant, session, repo, project, tech) -> tuple[str, str]:
        # The research engine is a heavy async run — never invoked silently.
        # A caller opts in by injecting an L7 retriever.
        return "", "research layer disabled by default (inject an L7 retriever to enable)"

    @staticmethod
    def _task_symbols(task: str) -> List[str]:
        """Candidate symbol names from the task text (PascalCase, camelCase,
        snake_case words). Honest heuristic — the code graph does the real
        matching."""
        out = set()
        for m in re.finditer(r"\b([A-Z][A-Za-z0-9_]{2,}|[a-z][a-z0-9_]{2,})\b", task):
            w = m.group(1)
            if w.lower() in {"the", "and", "for", "with", "from", "into", "then", "this", "that", "should", "using"}:
                continue
            out.add(w)
        return sorted(out)
