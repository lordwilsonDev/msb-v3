"""OpenAI-compatible adapter — the /v1 surface for OpenAI SDK clients.

Lets any OpenAI-compatible client (Open WebUI, LangChain, curl) talk to MSB
through the native harness, without replacing anything:

    Open WebUI  --OpenAI-compat-->  /v1  -->  ChatHarness  -->  Ollama/llama.cpp

Mounts at `/v1` (see app.py). Two endpoints, the minimum OpenAI contract:

    GET  /v1/models              -> {"object":"list","data":[{"id","object",...}]}
    POST /v1/chat/completions    -> OpenAI ChatCompletion JSON, or SSE when
                                    `stream` is true (one delta + [DONE]).

Auth is fail-closed via settings.openai_api_key (OPENAI_API_KEY env):
unset -> 503 "adapter closed"; mismatch -> 401. The key is read at request
time so tests can toggle it without import-order tricks.

Mapping (OpenAI -> MSB):
    messages[].role=="system" -> ChatRequest.system (last wins)
    messages[].role=="user"   -> ChatRequest.query (last user message)
    prior messages            -> ctx["history"] (same shape the native /chat
                                 builds from MemoryStore; the harness today
                                 consumes system + tools + query)
    tools[]                   -> ChatRequest.tools (OpenAI function defs
                                 flattened to the native ToolSpec contract)
    user                      -> MSB session (sanitized; "openai-ui" default)
    X-Tenant-ID header        -> tenant scoping, same as the native /chat

Known limitation (documented in docs/open-webui-adapter-v1.md): the harness's
tool loop is single-shot, so multi-turn context relies on MSB's memory store
(exactly like the native /chat endpoint), not on the message array.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from msb_v3.api.auth import bearer_gate
from msb_v3.api.chat import ToolSpec
from msb_v3.api.models import list_models
from msb_v3.core.config import settings
from msb_v3.core.rate_limit import RateLimiter
from msb_v3.observability.metrics import RATE_LIMIT_REJECTIONS

router = APIRouter(tags=["openai"])

# /v1 guards: per-client sliding-window caps. Window/max read live from
# settings (like auth) so config changes apply without a restart.
# - embeddings: each embedded item consumes one unit (a batch of N = N units)
# - chat: one unit per request (streaming requests count once)
_EMBED_LIMITER = RateLimiter(
    window_s=lambda: float(settings.openai_embed_rate_window_s),
    max_count=lambda: settings.openai_embed_rate_max,
)
_CHAT_LIMITER = RateLimiter(
    window_s=lambda: float(settings.openai_chat_rate_window_s),
    max_count=lambda: settings.openai_chat_rate_max,
)


# --- OpenAI request/response models (superset; unknown fields ignored) ---


class ChatMessage(BaseModel):
    role: str
    content: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Any]] = None
    tool_call_id: Optional[str] = None


class FunctionDef(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class ToolDef(BaseModel):
    type: str = "function"
    function: FunctionDef


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage]
    tools: Optional[List[ToolDef]] = None
    tool_choice: Optional[Any] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    n: Optional[int] = 1
    stream: Optional[bool] = False
    user: Optional[str] = None


class EmbeddingsRequest(BaseModel):
    model: Optional[str] = None
    input: Any  # str | list[str] (validated in the handler)


def _check_auth(request: Request) -> None:
    """Fail-closed bearer gate via the shared auth helper (Phase 3): closed
    (503) until OPENAI_API_KEY is set, then 401 on any mismatch. The key is
    read live from settings so a config change takes effect without a
    restart; comparison is constant-time (secrets.compare_digest)."""
    bearer_gate(
        request,
        settings.openai_api_key,
        "OPENAI_API_KEY not configured — /v1 adapter is closed (set it in .env)",
        "invalid API key",
    )


def _msb_tool(t: ToolDef) -> ToolSpec:
    """Flatten the OpenAI function-tool shape onto the native ToolSpec
    contract (/chat accepts {type,name,description,parameters})."""
    return ToolSpec(
        type=t.type,
        name=t.function.name,
        description=t.function.description,
        parameters=t.function.parameters,
    )


def _session_id(request: Request, req: ChatCompletionRequest) -> str:
    """OpenAI has no session concept. Derive one from the optional `user`
    field (sanitized) so distinct clients keep separate MSB memory sessions;
    tenant scoping matches the native /chat X-Tenant-ID behavior."""
    tenant = request.headers.get("X-Tenant-ID", "default")
    u = "".join(c if c.isalnum() or c in "._-" else "_" for c in (req.user or ""))[:64]
    session = u or "openai-ui"
    return f"{tenant}:{session}" if tenant != "default" else session


def _model_id(m: Dict[str, object]) -> str:
    """Display id for the model dropdown. llama.cpp models are configured by
    absolute GGUF path — never leak the filesystem path as the id."""
    if m["backend"] == "llamacpp":
        return os.path.basename(str(m["name"])).removesuffix(".gguf")
    return str(m["name"])


@router.get("/models")
async def openai_models(request: Request) -> Dict[str, Any]:
    """GET /v1/models — the model dropdown source for OpenAI clients.
    Includes the embedding model so Open WebUI can select it for document
    RAG (it is listed, not used for chat — chat routes by active backend)."""
    _check_auth(request)
    from msb_v3.api.rag import _EMBED_MODEL  # lazy: Ollama optional

    data = [
        {
            "id": _model_id(m),
            "object": "model",
            "created": 0,
            "owned_by": m["backend"],
            "active": m["active"],
        }
        for m in list_models()
    ]
    data.append(
        {
            "id": _EMBED_MODEL,
            "object": "model",
            "created": 0,
            "owned_by": "ollama",
            "active": False,
        }
    )
    return {"object": "list", "data": data}


@router.post("/embeddings")
async def openai_embeddings(request: Request, req: EmbeddingsRequest) -> Dict[str, Any]:
    """POST /v1/embeddings — OpenAI-compatible embeddings, backed by the same
    provider the native /rag uses (Ollama nomic-embed-text via rag._embed,
    including its long-text truncation retries). Accepts a single string or a
    batch; the model id is echoed and informational (routing uses the
    configured OLLAMA_EMBED_MODEL)."""
    _check_auth(request)
    from msb_v3.api.rag import _EMBED_MODEL, _embed  # lazy: Ollama optional

    items = [req.input] if isinstance(req.input, str) else req.input
    if not isinstance(items, list) or not items or not all(isinstance(t, str) and t for t in items):
        raise HTTPException(status_code=400, detail="input must be a non-empty string or list of strings")
    if len(items) > settings.openai_embed_max_batch:
        RATE_LIMIT_REJECTIONS.labels(limiter="embeddings", reason="batch").inc()
        raise HTTPException(
            status_code=413,
            detail=f"batch of {len(items)} exceeds max {settings.openai_embed_max_batch} items per request",
        )
    if not _EMBED_LIMITER.check(request, units=len(items)):
        RATE_LIMIT_REJECTIONS.labels(limiter="embeddings", reason="rate").inc()
        raise HTTPException(
            status_code=429,
            detail=(
                "rate_limit_exceeded (max "
                f"{settings.openai_embed_rate_max} embedding items per "
                f"{settings.openai_embed_rate_window_s}s)"
            ),
        )

    vectors: List[List[float]] = []
    for text in items:
        try:
            vectors.append(await _embed(text))
        except (RuntimeError, httpx.HTTPError) as exc:
            # rag._embed wraps retry exhaustion in RuntimeError, but raw
            # httpx failures (unreachable Ollama, timeout) propagate as-is
            raise HTTPException(status_code=502, detail=f"embedding backend failed: {exc}") from exc

    model = req.model or _EMBED_MODEL
    total_tokens = max(1, sum(len(t) // 4 for t in items))
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": vec}
            for i, vec in enumerate(vectors)
        ],
        "model": model,
        "usage": {"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    }


@router.post("/chat/completions")
async def chat_completions(request: Request, req: ChatCompletionRequest) -> Any:
    _check_auth(request)
    if req.n and req.n > 1:
        raise HTTPException(status_code=400, detail="n > 1 is not supported")
    # Rate guard after validation: an invalid request (400) never consumes
    # quota. One unit per request, streaming included. Refused before any
    # work reaches the harness.
    if not _CHAT_LIMITER.check(request, units=1):
        RATE_LIMIT_REJECTIONS.labels(limiter="chat", reason="rate").inc()
        raise HTTPException(
            status_code=429,
            detail=(
                "rate_limit_exceeded (max "
                f"{settings.openai_chat_rate_max} chat requests per "
                f"{settings.openai_chat_rate_window_s}s)"
            ),
        )

    system: Optional[str] = None
    query = ""
    history: List[str] = []
    for m in req.messages:
        content = m.content or ""
        if m.role == "system":
            system = content
        elif m.role == "user":
            query = content
            history.append(f"user: {content}")
        elif m.role == "assistant":
            history.append(f"assistant: {content}")

    ctx: Dict[str, Any] = {}
    if system:
        ctx["system"] = system
    if history:
        ctx["history"] = "\n".join(history)
    if req.tools:
        ctx["tools"] = [_msb_tool(t) for t in req.tools]

    app = request.app
    harness = getattr(app.state, "chat", None)
    if harness is None:
        from msb_v3.harnesses.base import ChatHarness

        harness = ChatHarness()
        app.state.chat = harness

    result = harness.execute(query, ctx, session=_session_id(request, req))
    text = result.payload.get("text", "")
    if not result.ok:
        raise HTTPException(status_code=500, detail=result.error or "harness failed")

    model = req.model or result.payload.get("model", "msb-v3")
    created = int(time.time())
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    usage = {
        "prompt_tokens": max(1, len(query) // 4),
        "completion_tokens": max(1, len(text) // 4),
        "total_tokens": max(1, (len(query) + len(text)) // 4),
    }

    if req.stream:
        # Spec-shaped SSE: role-only delta first, then the content delta, then
        # a final chunk carrying finish_reason="stop" — clients that close the
        # stream on that last chunk (OpenAI SDK, strict parsers) work cleanly.
        base = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
        }

        def _chunk(delta: Dict[str, Any], finish_reason: Any) -> str:
            return "data: " + json.dumps(
                {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
            ) + "\n\n"

        def _sse() -> Any:
            yield _chunk({"role": "assistant"}, None)
            yield _chunk({"content": text}, None)
            yield _chunk({}, "stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            _sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }
