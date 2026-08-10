"""Conversation router — POST /conversation/ask (the envelope) + the test-hook.

Implements docs/conversation-envelope-v1.md + conversation-e2e-harness-v1.md:
one synchronous endpoint, both modes, dual guardrails, sources[] with
freshness, the answer as a content-addressed claim_id, terminating in a §8
ledger artifact (producer.py). The stub profile (MSB_CONVERSATION_MODEL=stub)
is deterministic and zero-spend; the test-hook exposes the stub-invocation
counter so a black-box probe can assert zero model spend on BLOCK.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from msb_v3.conversation import producer
from msb_v3.conversation.envelope import (
    SCHEMA_VERSION,
    ConversationRequest,
    StubModel,
    build_record,
    claim_id_for_ans,
    compute_freshness,
    input_guardrail,
    mint_trace_id,
    model_mode,
    now_iso,
    output_guardrail,
    resolve_source_ts,
    validate_request,
)

router = APIRouter(tags=["conversation"])

# One shared stub instance for the whole app process: the endpoint increments
# invocations, the test-hook reads them. A BLOCK must never touch it.
_stub = StubModel()


def _check_auth(request: Request) -> None:
    """Live-auth gate, opt-in: enforced when MCP_BRIDGE_SECRET is set (CI
    seeds it and the probe sends x-mcp-secret — auth is supplied, never
    bypassed). Unset secret = dev mode, mirroring the rest of the app."""
    secret = os.getenv("MCP_BRIDGE_SECRET", "")
    if not secret:
        return
    if request.headers.get("x-mcp-secret") != secret:
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("/test-hook")
async def test_hook(request: Request) -> dict[str, Any]:
    """Zero-model-spend assertion surface (harness spec §3): the stub
    invocation counter. Active in stub mode; harmless in live mode."""
    _check_auth(request)
    return {
        "stub_mode": model_mode() == "stub",
        "stub_invocations": _stub.invocations,
    }


async def _retrieve(query: str, tenant_id: str, stub: bool) -> list[dict[str, Any]]:
    if stub:
        return _stub.retrieve(query)
    from msb_v3.retrieval.engine import RetrievalRouter

    result = await RetrievalRouter(tenant_id=tenant_id).run(query, top_k=5)
    sources: list[dict[str, Any]] = []
    for idx, m in enumerate(result.get("matches", [])):
        source_ts = resolve_source_ts(m.get("metadata"), m.get("source"))
        sources.append({
            "source_id": m.get("id") or f"hit:{idx}",
            "score": m.get("score"),
            "source": m.get("source"),
            "text": m.get("text"),
            "provenance": m.get("provenance"),
            "source_ts": source_ts,
            "freshness": compute_freshness(source_ts),
        })
    return sources


async def _compose(query: str, sources: list[dict[str, Any]], stub: bool) -> tuple[str, list[dict[str, Any]]]:
    """(answer_text, citations). Stub: deterministic, counted. Live: attempt
    the local model once; deterministic fallback otherwise (never blocks the
    response on a dead model)."""
    if stub:
        return _stub.compose(query, sources)
    if not sources:
        return "I could not find supporting evidence in the retrieved sources.", []
    excerpt = str(sources[0].get("text", ""))[:200]
    citations = [{"source_id": s["source_id"]} for s in sources]
    try:
        from msb_v3.local_ai.ollama import LocalAIClient

        client = LocalAIClient()
        prompt = (
            "Answer the question concisely. Ground your answer in the sources.\n\n"
            f"Sources:\n- {excerpt}\n\nQuestion: {query}"
        )
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None, lambda: client.generate(prompt, max_tokens=200)
        )
        text = (getattr(resp, "text", "") or "").strip()
        if text:
            return text, citations
    except Exception:
        pass
    return f"[fallback] Based on the retrieved sources: {excerpt}", citations


def _error_body(trace_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "trace_id": trace_id,
        "status": "error",
        "error": {"code": code, "message": message},
    }


def _envelope_error(status_code: int, trace_id: str, code: str, message: str) -> JSONResponse:
    """Error responses carry the envelope body directly (spec §8), not
    wrapped in FastAPI's {'detail': ...}."""
    return JSONResponse(status_code=status_code, content=_error_body(trace_id, code, message))


@router.post("/ask")
async def conversation_ask(body: ConversationRequest, request: Request) -> dict[str, Any]:
    """The envelope — one request handler, both modes (spec §3/§4/§5).

    `sources_hint` is accepted for legacy RunRequest passthrough compatibility
    and intentionally NOT consumed in v1: retrieval is driven by the
    RetrievalRouter (live) or the stub fixtures (CI), never by client-supplied
    file paths. Wiring it would create an unauthenticated file-read surface.
    """
    _check_auth(request)
    trace_id = body.trace_id or mint_trace_id()
    started = time.perf_counter()

    error = validate_request(body)
    if error:
        return _envelope_error(422, trace_id, error["code"], error["message"])

    # Task Contract validation (docs/task-contract-v1.md): workflow-mode dag
    # entries must satisfy the contract — caps enforced at validation, not at
    # runtime. Invalid contracts are rejected, never half-served.
    if body.mode == "workflow" and body.workflow is not None:
        from msb_v3.conversation.task_contract import validate_workflow

        contract_errors = validate_workflow(body.workflow.model_dump())
        if contract_errors:
            return _envelope_error(422, trace_id, "contract_invalid", "; ".join(contract_errors))

    stub = model_mode() == "stub"
    input_v = input_guardrail(body.query)
    if stub and _stub.is_block_query(body.query):
        input_v = {
            "verdict": "BLOCK", "policy": "stub-fixture-v1",
            "reason": "stub://blocked fixture", "checked_at": now_iso(),
        }

    # --- input BLOCK short-circuits before retrieval / composition ---
    if input_v["verdict"] == "BLOCK":
        latency_ms = int((time.perf_counter() - started) * 1000)
        record = build_record(
            trace_id=trace_id, mode=body.mode, query=body.query, status="blocked",
            input_guardrail=input_v,
            output_guardrail={"verdict": None, "citation_rate": None, "reason": None},
            sources=[], answer=None, latency_ms=latency_ms,
            git_head=producer.default_git_head(),
            tenant_id=body.tenant_id, session_id=body.session_id,
            workflow_dag=(body.workflow.dag if body.workflow else None),
        )
        try:
            producer.append_record(record, producer.default_ledger_dir())
            result = producer.produce(record, producer.default_ledger_dir(), record["git_head"])
        except (ValueError, OSError) as exc:
            return _envelope_error(503, trace_id, "ledger_write_failed", f"log hop failed: {exc}")
        return {
            "schema_version": SCHEMA_VERSION,
            "trace_id": trace_id,
            "mode": body.mode,
            "status": "blocked",
            "query": body.query,
            "input_guardrail": input_v,
            "sources": None,
            "output_guardrail": {"verdict": None, "citation_rate": None, "reason": None},
            "answer": None,
            "evidence_ref": result["evidence_ref"],
            "latency_ms": latency_ms,
        }

    # --- retrieve (stub fixtures or the real RetrievalRouter) ---
    try:
        sources = await _retrieve(body.query, body.tenant_id, stub)
    except Exception as exc:  # noqa: BLE001 — model/db down → 503, retryable
        return _envelope_error(503, trace_id, "unavailable", f"retrieval failed: {exc}")

    # --- compose (the model hop; stub is deterministic and counted) ---
    answer_text, citations = await _compose(body.query, sources, stub)

    # --- output guardrail (guards the drafted answer vs its sources) ---
    out_v = output_guardrail(sources, citations, answer_text)
    answer: Optional[dict[str, Any]] = None
    if out_v["verdict"] != "BLOCKED":
        source_ids = [s["source_id"] for s in sources]
        answer = {
            "text": answer_text,
            "text_excerpt": answer_text[:200],
            "claim_id": claim_id_for_ans(body.query, source_ids, answer_text),
            "citations": citations,
        }

    latency_ms = int((time.perf_counter() - started) * 1000)
    record = build_record(
        trace_id=trace_id, mode=body.mode, query=body.query,
        status="answered",
        input_guardrail=input_v, output_guardrail=out_v,
        sources=sources, answer=answer, latency_ms=latency_ms,
        git_head=producer.default_git_head(),
        tenant_id=body.tenant_id, session_id=body.session_id,
        workflow_dag=(body.workflow.dag if body.workflow else None),
    )
    try:
        producer.append_record(record, producer.default_ledger_dir())
        result = producer.produce(record, producer.default_ledger_dir(), record["git_head"])
    except (ValueError, OSError) as exc:
        return _envelope_error(503, trace_id, "ledger_write_failed", f"log hop failed: {exc}")

    return {
        "schema_version": SCHEMA_VERSION,
        "trace_id": trace_id,
        "mode": body.mode,
        "status": "answered",
        "query": body.query,
        "input_guardrail": input_v,
        "sources": sources,
        "output_guardrail": out_v,
        "answer": answer,
        "evidence_ref": result["evidence_ref"],
        "latency_ms": latency_ms,
    }
