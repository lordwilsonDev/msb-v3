"""Tests for the OpenAI-compatible /v1 adapter (msb_v3.api.openai_compat).

Driven with a fake harness so no real model call happens: the adapter must
map OpenAI requests onto the native ChatHarness contract and emit OpenAI
responses (JSON + SSE). Auth is fail-closed: 503 while OPENAI_API_KEY is
unset, 401 on mismatch.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.core.config import settings  # noqa: E402
from msb_v3.harnesses.base import HarnessResult  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_embed_rate_window() -> None:
    """The /v1/embeddings limiter is module-level state shared across tests;
    clear it so earlier tests' consumption never bleeds into later ones."""
    from msb_v3.api.openai_compat import _EMBED_LIMITER

    _EMBED_LIMITER.clear()
    yield
    _EMBED_LIMITER.clear()


class _FakeHarness:
    """Records every (query, context, kwargs) and echoes the query back."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []

    def execute(self, query: str, context: dict | None = None, **kwargs: object) -> HarnessResult:
        self.calls.append((query, context or {}, kwargs))
        return HarnessResult(
            ok=True,
            event="chat:completed",
            payload={"query": query, "text": f"echo: {query}", "model": "test-model"},
        )


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.delenv("MCP_BRIDGE_SECRET", raising=False)
    from msb_v3.api.app import create_app

    app = create_app()
    app.state.chat = _FakeHarness()
    return TestClient(app)


def _chat_body(**overrides: object) -> dict:
    body = {"model": "test-model", "messages": [{"role": "user", "content": "hello"}]}
    body.update(overrides)
    return body


# --- auth: fail-closed ------------------------------------------------------


def test_503_when_adapter_unconfigured(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "")
    r = client.get("/v1/models")
    assert r.status_code == 503
    r = client.post("/v1/chat/completions", json=_chat_body())
    assert r.status_code == 503


def test_401_wrong_or_missing_key(client) -> None:
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers={"authorization": "Bearer nope"}).status_code == 401
    r = client.post("/v1/chat/completions", json=_chat_body(), headers={"authorization": "Bearer test-key"})
    assert r.status_code == 200


# --- models -----------------------------------------------------------------


def test_models_list(client) -> None:
    r = client.get("/v1/models", headers={"authorization": "Bearer test-key"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "list"
    assert len(body["data"]) >= 1
    for m in body["data"]:
        assert m["object"] == "model"
        assert m["id"]
        assert m["owned_by"] in {"ollama", "llamacpp"}
        # display ids never leak filesystem paths (llama.cpp GGUFs are paths)
        assert "/" not in m["id"]
        assert not m["id"].endswith(".gguf")


# --- chat completions (non-stream) -----------------------------------------


def test_completion_maps_and_returns_openai_schema(client) -> None:
    harness = client.app.state.chat
    r = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer test-key"},
        json=_chat_body(
            messages=[
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hello"},
            ],
            user="alice",
        ),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert body["model"] == "test-model"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "echo: hello"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] >= 1

    query, ctx, kwargs = harness.calls[-1]
    assert query == "hello"
    assert ctx["system"] == "be brief"
    assert kwargs["session"] == "alice"


def test_history_and_tools_passthrough(client) -> None:
    harness = client.app.state.chat
    r = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer test-key"},
        json=_chat_body(
            messages=[
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "second"},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "vault_read",
                        "description": "read a vault file",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                }
            ],
        ),
    )
    assert r.status_code == 200, r.text
    query, ctx, _ = harness.calls[-1]
    assert query == "second"  # last user message wins
    assert "user: first" in ctx["history"]
    assert "assistant: ok" in ctx["history"]
    tool = ctx["tools"][0]
    # OpenAI function def flattened onto the native ToolSpec contract
    assert tool.name == "vault_read"
    assert tool.description == "read a vault file"
    assert tool.parameters["properties"]["path"]["type"] == "string"


def test_default_session_and_tenant_scoping(client) -> None:
    harness = client.app.state.chat
    client.post("/v1/chat/completions", headers={"authorization": "Bearer test-key"}, json=_chat_body())
    _, _, kwargs = harness.calls[-1]
    assert kwargs["session"] == "openai-ui"

    client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer test-key", "X-Tenant-ID": "tenant:beta"},
        json=_chat_body(user="bob"),
    )
    _, _, kwargs = harness.calls[-1]
    assert kwargs["session"] == "tenant:beta:bob"  # raw tenant prefix, same as the native /chat


def test_n_greater_than_one_rejected(client) -> None:
    r = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer test-key"},
        json=_chat_body(n=3),
    )
    assert r.status_code == 400


# --- embeddings -------------------------------------------------------------


async def _fake_embed(text: str) -> list[float]:
    return [0.1, 0.2, 0.3]


async def _failing_embed(text: str) -> list[float]:
    raise RuntimeError("ollama unreachable")


async def _down_embed(text: str) -> list[float]:
    # real failure mode when Ollama is unreachable: raw httpx error escapes
    # rag._embed (only the final retry wraps to RuntimeError)
    raise httpx.ConnectError("connection refused")


@pytest.fixture()
def embed_client(client, monkeypatch) -> TestClient:
    import msb_v3.api.rag as rag

    monkeypatch.setattr(rag, "_embed", _fake_embed)
    return client


def _emb(body: dict) -> dict:
    return {"model": "nomic-embed-text", "input": "hello world", **body}


def test_embeddings_string_input(embed_client) -> None:
    r = embed_client.post("/v1/embeddings", headers={"authorization": "Bearer test-key"}, json=_emb({}))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 1
    assert body["data"][0] == {"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}
    assert body["model"] == "nomic-embed-text"
    assert body["usage"]["total_tokens"] >= 1


def test_embeddings_batch_input(embed_client) -> None:
    r = embed_client.post(
        "/v1/embeddings",
        headers={"authorization": "Bearer test-key"},
        json=_emb({"input": ["a", "bb", "ccc"]}),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [d["index"] for d in body["data"]] == [0, 1, 2]
    assert all(d["embedding"] == [0.1, 0.2, 0.3] for d in body["data"])


def test_embeddings_rejects_empty_or_non_string(client) -> None:
    for bad in ([], ["ok", 5], [""], ""):
        r = client.post(
            "/v1/embeddings",
            headers={"authorization": "Bearer test-key"},
            json=_emb({"input": bad}),
        )
        assert r.status_code == 400, (bad, r.text)


def test_embeddings_model_echo(client, monkeypatch) -> None:
    import msb_v3.api.rag as rag

    monkeypatch.setattr(rag, "_embed", _fake_embed)
    r = client.post(
        "/v1/embeddings",
        headers={"authorization": "Bearer test-key"},
        json=_emb({"model": "my-custom-id", "input": "x"}),
    )
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "my-custom-id"


def test_embeddings_backend_failure_is_502(client, monkeypatch) -> None:
    import msb_v3.api.rag as rag

    monkeypatch.setattr(rag, "_embed", _failing_embed)
    r = client.post("/v1/embeddings", headers={"authorization": "Bearer test-key"}, json=_emb({}))
    assert r.status_code == 502
    assert "ollama unreachable" in r.json()["detail"]


def test_embeddings_raw_httpx_failure_is_502(client, monkeypatch) -> None:
    """Unreachable Ollama surfaces as a raw httpx error from rag._embed; it
    must still map to 502, not leak a 500."""
    import msb_v3.api.rag as rag

    monkeypatch.setattr(rag, "_embed", _down_embed)
    r = client.post("/v1/embeddings", headers={"authorization": "Bearer test-key"}, json=_emb({}))
    assert r.status_code == 502
    assert "connection refused" in r.json()["detail"]


# --- rate limit + batch cap -------------------------------------------------


def test_embeddings_batch_over_cap_is_413(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_embed_max_batch", 2)
    r = client.post(
        "/v1/embeddings",
        headers={"authorization": "Bearer test-key"},
        json=_emb({"input": ["a", "b", "c"]}),
    )
    assert r.status_code == 413
    assert "exceeds max 2" in r.json()["detail"]


def test_embeddings_batch_at_cap_passes(client, monkeypatch) -> None:
    import msb_v3.api.rag as rag

    monkeypatch.setattr(settings, "openai_embed_max_batch", 2)
    monkeypatch.setattr(rag, "_embed", _fake_embed)
    r = client.post(
        "/v1/embeddings",
        headers={"authorization": "Bearer test-key"},
        json=_emb({"input": ["a", "b"]}),
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]) == 2


def test_embeddings_rate_exhaustion_is_429(client, monkeypatch) -> None:
    """A batch of N items consumes N units toward the per-client window cap;
    once the cap is hit the client is refused with 429."""
    import msb_v3.api.rag as rag

    monkeypatch.setattr(settings, "openai_embed_rate_max", 3)
    monkeypatch.setattr(settings, "openai_embed_rate_window_s", 60)
    monkeypatch.setattr(rag, "_embed", _fake_embed)
    headers = {"authorization": "Bearer test-key"}

    r = client.post("/v1/embeddings", headers=headers, json=_emb({"input": ["a", "b"]}))
    assert r.status_code == 200, r.text
    # next request would take the count to 4 > 3 -> refused before any work
    r = client.post("/v1/embeddings", headers=headers, json=_emb({"input": ["a", "b"]}))
    assert r.status_code == 429
    assert "rate_limit_exceeded" in r.json()["detail"]


def test_embeddings_rate_window_resets(client, monkeypatch) -> None:
    """After the window elapses the counter resets and the client is served
    again."""
    import msb_v3.api.rag as rag
    from msb_v3.api.openai_compat import _EMBED_LIMITER

    monkeypatch.setattr(settings, "openai_embed_rate_max", 1)
    monkeypatch.setattr(settings, "openai_embed_rate_window_s", 60)
    monkeypatch.setattr(rag, "_embed", _fake_embed)
    headers = {"authorization": "Bearer test-key"}

    assert client.post("/v1/embeddings", headers=headers, json=_emb({})).status_code == 200
    assert client.post("/v1/embeddings", headers=headers, json=_emb({})).status_code == 429

    # age this client's window start into the past -> next request is served.
    # Starlette's TestClient always presents the peer host as "testclient".
    _EMBED_LIMITER.table["testclient"] = (time.time() - 61, 1)
    assert client.post("/v1/embeddings", headers=headers, json=_emb({})).status_code == 200


def test_models_includes_embed_model(client) -> None:
    import msb_v3.api.rag as rag

    r = client.get("/v1/models", headers={"authorization": "Bearer test-key"})
    ids = [m["id"] for m in r.json()["data"]]
    assert rag._EMBED_MODEL in ids


def test_rate_limit_rejection_increments_prometheus_counter(client, monkeypatch) -> None:
    """A 429 from /v1/embeddings is visible on the Prometheus scrape."""
    import msb_v3.api.rag as rag
    from msb_v3.observability.metrics import RATE_LIMIT_REJECTIONS

    monkeypatch.setattr(settings, "openai_embed_rate_max", 0)  # every request is refused
    monkeypatch.setattr(rag, "_embed", _fake_embed)
    headers = {"authorization": "Bearer test-key"}

    rate_before = RATE_LIMIT_REJECTIONS.labels(limiter="embeddings", reason="rate")._value.get()
    assert client.post("/v1/embeddings", headers=headers, json=_emb({})).status_code == 429
    rate_after = RATE_LIMIT_REJECTIONS.labels(limiter="embeddings", reason="rate")._value.get()
    assert rate_after == rate_before + 1

    r = client.get("/metrics/prometheus")
    assert r.status_code == 200
    # TestClient escapes quotes in the body; assert via the counter's live
    # value instead of brittle text matching.
    assert "msb_v3_rate_limit_rejections_total" in r.text
    assert RATE_LIMIT_REJECTIONS.labels(limiter="embeddings", reason="rate")._value.get() == rate_after


def test_batch_cap_rejection_counts_on_prometheus(client, monkeypatch) -> None:
    """The 413 batch-cap refusal is surfaced too, under reason="batch"."""
    import msb_v3.api.rag as rag
    from msb_v3.observability.metrics import RATE_LIMIT_REJECTIONS

    monkeypatch.setattr(settings, "openai_embed_max_batch", 2)
    monkeypatch.setattr(rag, "_embed", _fake_embed)
    headers = {"authorization": "Bearer test-key"}

    batch_before = RATE_LIMIT_REJECTIONS.labels(limiter="embeddings", reason="batch")._value.get()
    rate_before = RATE_LIMIT_REJECTIONS.labels(limiter="embeddings", reason="rate")._value.get()
    assert client.post("/v1/embeddings", headers=headers, json=_emb({"input": ["a", "b", "c"]})).status_code == 413
    assert RATE_LIMIT_REJECTIONS.labels(limiter="embeddings", reason="batch")._value.get() == batch_before + 1
    # the 413 must NOT also consume the rate window
    assert RATE_LIMIT_REJECTIONS.labels(limiter="embeddings", reason="rate")._value.get() == rate_before


# --- streaming --------------------------------------------------------------


def test_stream_returns_spec_shaped_sse_with_done(client) -> None:
    """Spec shape: role-only delta, then the content delta, then a final
    chunk with finish_reason="stop", then [DONE]."""
    r = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer test-key"},
        json=_chat_body(stream=True),
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text.rstrip().endswith("data: [DONE]")
    data_lines = [ln for ln in r.text.splitlines() if ln.startswith("data: ") and ln != "data: [DONE]"]
    chunks = [json.loads(ln[len("data: "):]) for ln in data_lines]
    assert [c["choices"][0]["delta"] for c in chunks] == [
        {"role": "assistant"},
        {"content": "echo: hello"},
        {},
    ]
    assert [c["choices"][0]["finish_reason"] for c in chunks] == [None, None, "stop"]
    assert chunks[0]["object"] == "chat.completion.chunk"
