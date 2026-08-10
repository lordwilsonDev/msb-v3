"""Conversation envelope v1 — the governing contract for the conversation interface.

Implements docs/conversation-envelope-v1.md: one synchronous request/response
envelope shared by chat and workflow modes, dual guardrails (input ALLOW/BLOCK,
output SUPPORTING/UNSUPPORTED/BLOCKED), sources[] with score + provenance +
source_ts + freshness, the answer as a content-addressed claim_id, terminating
in a §8 ledger artifact via the conversation producer (producer.py).

The claim_id canonicalization here is pinned IDENTICALLY in
conversation-ledger-producer-v1.md — never change one without the other.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"
SCHEMA_MAJOR = SCHEMA_VERSION.split(".")[0]

# --- claim_id canonicalization (pinned identically across all three specs) ---


def canonical_json(obj: Any) -> str:
    """Deterministic JSON serialization for content-addressing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def claim_id_for_ans(query: str, source_ids: list[str], answer_text: str) -> str:
    """claim:ans:<hash> — epistemic: is this answer, as stated, supported by these sources?

    Hash base is the SORTED source_id list, never the full source objects
    (score/provenance/source_ts are evidence, not identity — and they vary
    with retrieval, which would break determinism). Sorting makes RRF
    tie-order jitter impossible to change a claim_id for the same event.
    """
    base = canonical_json({
        "query": query,
        "source_ids": sorted(source_ids),
        "answer_text": answer_text,
    })
    return f"claim:ans:{_sha256(base)[:12]}"


def claim_id_for_query(query: str) -> str:
    """claim:ok:query:<hash> — availability: is this query safely answerable?"""
    base = canonical_json({"query": query})
    return f"claim:ok:query:{_sha256(base)[:12]}"


# --- freshness (computed at answer time; never stored as authoritative truth) ---

_FRESH_DAYS = int(os.getenv("MSB_CONVERSATION_FRESH_DAYS", "30"))
_AGING_DAYS = int(os.getenv("MSB_CONVERSATION_AGING_DAYS", "90"))


def _parse_ts(value: Any) -> Optional[datetime.datetime]:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.datetime.fromtimestamp(float(value), tz=datetime.timezone.utc)
        text = str(value).strip()
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError, OverflowError):
        return None


def _iso_utc(ts: datetime.datetime) -> str:
    return ts.astimezone(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def now_iso() -> str:
    return _iso_utc(datetime.datetime.now(datetime.timezone.utc))


def source_ts_from_metadata(metadata: Optional[dict]) -> Optional[str]:
    """source_ts priority order: metadata.created_at / metadata.ts / timestamp / created.

    Returns an ISO-8601 UTC string or None — a missing timestamp is recorded
    as freshness UNKNOWN, never guessed.
    """
    for key in ("created_at", "ts", "timestamp", "created"):
        ts = _parse_ts((metadata or {}).get(key))
        if ts is not None:
            return _iso_utc(ts)
    return None


def source_ts_from_file(source: Optional[str]) -> Optional[str]:
    """Filesystem-backed sources fall back to file mtime."""
    if not source:
        return None
    try:
        p = Path(source)
        if p.exists() and p.is_file():
            return _iso_utc(datetime.datetime.fromtimestamp(p.stat().st_mtime, tz=datetime.timezone.utc))
    except OSError:
        pass
    return None


def resolve_source_ts(metadata: Optional[dict], source: Optional[str]) -> Optional[str]:
    """§6 priority: metadata first, file mtime second, None (→ UNKNOWN) last."""
    ts = source_ts_from_metadata(metadata)
    if ts is not None:
        return ts
    return source_ts_from_file(source)


def compute_freshness(source_ts: Optional[str]) -> str:
    """FRESH ≤30d | AGING 31–90d | STALE >90d | UNKNOWN (no/unknowable ts).

    UNKNOWN is treated as STALE by worst-source-wins (§6): an answer citing
    only UNKNOWN sources may not be SUPPORTING.
    """
    if not source_ts:
        return "UNKNOWN"
    ts = _parse_ts(source_ts)
    if ts is None:
        return "UNKNOWN"
    age_days = (datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds() / 86400.0
    if age_days <= _FRESH_DAYS:
        return "FRESH"
    if age_days <= _AGING_DAYS:
        return "AGING"
    return "STALE"


# --- input guardrail (guards the QUERY, before retrieval) ---

# Same blocklist discipline as /research/assistant/run (_SAFETY_BLOCKLIST):
# regex + label, ALLOW/BLOCK. Kept in one place here; the research router
# keeps its own copy for its legacy contract.
_SAFETY_BLOCKLIST = [
    (re.compile(r"how\s+to\s+(make|build|create)\s+a\s+(bomb|weapon|explosive|malware|ransomware|virus)", re.I), "dangerous/weapon instruction blocked"),
    (re.compile(r"(instruction|guide)\s+to\s+(harm|injure|kill|attack)", re.I), "harm instruction blocked"),
    (re.compile(r"(bypass|disable|hack).+(security|authentication|verification|firewall|antivirus)", re.I), "security bypass blocked"),
]


def input_guardrail(query: str) -> dict[str, Any]:
    """Guard the query. BLOCK short-circuits before retrieval (zero model spend)."""
    for pattern, label in _SAFETY_BLOCKLIST:
        if pattern.search(query or ""):
            return {
                "verdict": "BLOCK", "policy": "safety-blocklist-v1",
                "reason": label, "checked_at": now_iso(),
            }
    return {
        "verdict": "ALLOW", "policy": "safety-blocklist-v1",
        "reason": None, "checked_at": now_iso(),
    }


# --- output guardrail (guards the DRAFTED ANSWER vs its sources) ---


def _citation_rate(sources: list[dict], citations: list[dict]) -> float:
    """cited source_ids / total sources. Memory citations don't count toward
    the rate (they're exempt from the no-ghost-citation invariant, not support)."""
    if not sources:
        return 0.0
    cited = sum(
        1 for s in sources
        if any(c.get("source_id") == s["source_id"] for c in citations)
    )
    return cited / len(sources)


def output_guardrail(
    sources: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    answer_text: str,
) -> dict[str, Any]:
    """Guard the drafted answer vs its sources.

    - BLOCKED: the drafted answer itself hits the blocklist → no answer
      released, evidence still logged (maps to claim:ok:query CONTRADICTING).
    - SUPPORTING: citation_rate >= 0.5 AND at least one CITED source is
      FRESH/AGING (worst-source-wins: STALE/UNKNOWN-only answers can't be
      SUPPORTING) AND citations non-empty.
    - UNSUPPORTED: everything else — incl. memory-only answers (no sources[]
      means no SUPPORTING, per §6).
    """
    for pattern, label in _SAFETY_BLOCKLIST:
        if pattern.search(answer_text or ""):
            return {
                "verdict": "BLOCKED",
                "citation_rate": _citation_rate(sources, citations),
                "reason": label,
            }
    rate = _citation_rate(sources, citations)
    cited_ids = {c.get("source_id") for c in citations if c.get("source_id")}
    cited_sources = [s for s in sources if s["source_id"] in cited_ids]
    has_fresh = any(s.get("freshness") in ("FRESH", "AGING") for s in cited_sources)
    if sources and rate >= 0.5 and citations and has_fresh:
        verdict = "SUPPORTING"
    else:
        verdict = "UNSUPPORTED"
    return {"verdict": verdict, "citation_rate": round(rate, 4), "reason": None}


# --- deterministic stub model (the CI model hop — zero spend, zero network) ---

STUB_MODEL_ENV = "MSB_CONVERSATION_MODEL"
STUB_MODE = "stub"
LIVE_MODE = "ollama"


def model_mode() -> str:
    """'stub' (CI profile) | 'ollama' (live, local only). Unset defaults to
    live so the running server never silently serves stub answers; CI and
    tests select stub explicitly."""
    return os.getenv(STUB_MODEL_ENV, LIVE_MODE)


def _now_fresh_ts(days_ago: int) -> str:
    """Fixture source_ts: now truncated to MIDNIGHT UTC minus days_ago.

    Truncation to the day keeps the stub byte-for-byte deterministic for any
    two requests within the same day (the harness's determinism contract)
    while the offset keeps the freshness band valid forever (0-29 days is
    always FRESH, 200 days is always STALE). A pure wall-clock timestamp
    would drift across second boundaries and break stub determinism.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return _iso_utc(midnight - datetime.timedelta(days=days_ago))


def _stub_sources(kind: str, seed: str) -> list[dict[str, Any]]:
    """Fixture sources for the stub. Deterministic given (kind, seed)."""
    prov = [{"index": "vector", "weight": 0.5, "rank": 1}]
    if kind == "STALE_ONLY":
        return [{
            "source_id": f"stub:old-{_sha256(seed)[:6]}",
            "source": "fixture/stale-note.md",
            "text": "A fixture source older than the freshness policy.",
            "score": 0.61,
            "provenance": prov,
            "source_ts": _now_fresh_ts(200),
            "freshness": "STALE",
        }]
    return [
        {
            "source_id": "note:2026/07/28-fox-valley",
            "source": "Documents/Vault/10_Projects/FoxValley.md",
            "text": "The Fox Valley retainer documents the founding client program.",
            "score": 0.83,
            "provenance": prov,
            "source_ts": _now_fresh_ts(10),
            "freshness": "FRESH",
        },
        {
            "source_id": "note:2026/08/01-retainer",
            "source": "Documents/Vault/10_Projects/Retainer.md",
            "text": "Retainer scope and deliverables are recorded in the vault.",
            "score": 0.77,
            "provenance": prov,
            "source_ts": _now_fresh_ts(6),
            "freshness": "FRESH",
        },
    ]


class StubModel:
    """Deterministic function of the request, not a canned string.

    Same request ⇒ byte-for-byte same response. The invocation counter is the
    zero-model-spend assertion surface (exposed via /conversation/test-hook).
    """

    def __init__(self) -> None:
        self.invocations = 0

    def is_block_query(self, query: str) -> bool:
        """Input-level BLOCK fixtures short-circuit before ANY stub call.

        Exact fixture: 'stub://blocked-answer' is the OUTPUT-BLOCK fixture
        (the stub DOES run), so it must not match the input-BLOCK prefix.
        """
        q = (query or "").strip().lower()
        return q.startswith("stub://blocked") and not q.startswith("stub://blocked-answer")

    def kind_for(self, query: str) -> str:
        q = (query or "").strip().lower()
        if q.startswith("stub://blocked-answer"):
            return "BLOCKED"
        if q.startswith("stub://unsupported"):
            return "UNSUPPORTED"
        if q.startswith("stub://stale-only"):
            return "STALE_ONLY"
        if q.startswith("stub://memory"):
            return "MEMORY"
        return "SUPPORTING"

    def retrieve(self, query: str) -> list[dict[str, Any]]:
        """The stub's retrieval hop: shaped fixture sources (deterministic)."""
        kind = self.kind_for(query)
        if kind in ("UNSUPPORTED", "MEMORY", "BLOCKED"):
            return []
        return _stub_sources(kind, query)

    def compose(self, query: str, sources: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        """(answer_text, citations) — the stub's model hop. Counted."""
        self.invocations += 1
        kind = self.kind_for(query)
        if kind == "UNSUPPORTED":
            return "Stub answer: no retrieved source supports this query.", []
        if kind == "STALE_ONLY":
            sid = sources[0]["source_id"] if sources else "stub:old"
            return "Stub answer: this answer relies on an outdated source.", [{"source_id": sid}]
        if kind == "MEMORY":
            return "Stub answer: answered from session memory.", [{"memory_ref": "mem_stub_1"}]
        if kind == "BLOCKED":
            # Drafted answer that trips the output blocklist (pattern 2:
            # "instructions to harm …"). Must be a REAL blocklist hit — the
            # fixture proves the output guardrail actually fires.
            return "Stub answer: instruction to harm a person follows.", [{"source_id": "note:2026/07/28-fox-valley"}]
        # SUPPORTING path
        return (
            "Stub answer: the retrieved sources support this response.",
            [{"source_id": s["source_id"]} for s in sources],
        )


# --- request model (validated, unknown fields rejected — fail fast) ---


class WorkflowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    dag: list[dict[str, Any]] = Field(default_factory=list)
    step_tracker: Optional[dict[str, Any]] = None


class ConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    trace_id: Optional[str] = None
    mode: str = "chat"
    query: str
    tenant_id: str = "default"
    session_id: Optional[str] = None
    workflow: Optional[WorkflowSpec] = None
    client: Optional[dict[str, str]] = None
    sources_hint: list[str] = Field(default_factory=list)


def mint_trace_id() -> str:
    return f"tr_{uuid.uuid4().hex[:12]}"


def validate_request(body: ConversationRequest) -> Optional[dict]:
    """Contract validation → error body (code) or None when valid.

    Fail fast with a 422: schema major mismatch, bad mode, empty query,
    missing workflow block for workflow mode.
    """
    if body.schema_version.split(".")[0] != SCHEMA_MAJOR:
        return {
            "code": "schema_mismatch",
            "message": f"schema_version {body.schema_version} unsupported; server supports {SCHEMA_VERSION}",
        }
    if body.mode not in ("chat", "workflow"):
        return {"code": "validation_failed", "message": f"mode must be 'chat' or 'workflow', got {body.mode!r}"}
    if not body.query or not body.query.strip():
        return {"code": "validation_failed", "message": "query must be a non-empty string"}
    if body.mode == "workflow" and body.workflow is None:
        return {"code": "validation_failed", "message": "workflow is required when mode == 'workflow'"}
    return None


# --- conversation record (the log hop's payload; record is source of truth) ---


def build_record(
    *,
    trace_id: str,
    mode: str,
    query: str,
    status: str,
    input_guardrail: dict[str, Any],
    output_guardrail: dict[str, Any],
    sources: list[dict[str, Any]],
    answer: Optional[dict[str, Any]],
    latency_ms: int,
    git_head: str,
    tenant_id: str = "default",
    session_id: Optional[str] = None,
    workflow_dag: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """The producer's input — every artifact field derives from this record,
    and NOTHING from produce-time state (timestamp = recorded_at here)."""
    return {
        "record_version": "1.0",
        "trace_id": trace_id,
        "mode": mode,
        "query": query,
        "status": status,
        "input_guardrail": input_guardrail,
        "output_guardrail": output_guardrail,
        "sources": [
            {
                "source_id": s["source_id"],
                "score": s.get("score"),
                "source_ts": s.get("source_ts"),
                "freshness": s.get("freshness"),
            }
            for s in sources
        ],
        "answer": answer,
        "latency_ms": latency_ms,
        "git_head": git_head,
        "model": model_mode(),
        "tenant_id": tenant_id,
        "session_id": session_id,
        "workflow_dag": workflow_dag,
        "recorded_at": now_iso(),
    }
