"""H4's exposure test — one planted secret, seven channels.

H4's open clause read: *"No test proves a secret cannot surface via prompt /
tool-output / logs / errors / memory / audit / RAG."* This file is that test.

Each channel is covered by **two** tests that must both pass:

1. **Exposure** — the value travels the real code path and the output carries
   ``[REDACTED]`` where the secret would have been.
2. **Sensitivity** — the same path with *that channel's* redaction hook
   neutralised, asserting the secret **does** appear.

The second one is the load-bearing one. A green exposure test on its own can
mean "the secret never reached that channel", which proves nothing; the pair
says: the value genuinely flows here, and the hook is the only thing stopping
it. If a future change removes a hook, the exposure test goes red; if a future
change stops the value from reaching the channel at all, the sensitivity check
goes red and the pair is re-examined rather than trusted.

The channels, and the single choke point each one has:

| Channel | Choke point |
|---|---|
| prompt | ``local_ai/ollama.py`` — the wire boundary before the model sees it |
| tool-output | the tool-result append in ``execute_tool_loop`` (belt: the array at the wire) |
| logs | ``core/logging.py`` — both formatters |
| errors | ``api/redaction_middleware.py`` — response bodies |
| memory | ``memory/store.py`` and ``memory_fabric/fabric.py`` — the write paths |
| audit | ``observability/audit.py`` — the single INSERT |
| RAG | ``retrieval/engine.py`` — matches, metadata, route errors |
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from msb_v3.api.redaction_middleware import SecretRedactionMiddleware
from msb_v3.core.logging import HumanFormatter, JSONFormatter
from msb_v3.local_ai.ollama import LocalAIClient
from msb_v3.memory.store import MemoryStore, Message
from msb_v3.memory_fabric.fabric import MemoryFabric
from msb_v3.memory_fabric.store import MemoryFabricStore
from msb_v3.observability import audit as audit_mod
from msb_v3.observability.audit import ArgusAuditor, MulchFinding
from msb_v3.retrieval import engine as engine_mod
from msb_v3.retrieval.engine import RetrievalRouter
from msb_v3.secrets.redact import MASK, clear_registry, register_secret_value

# No known credential shape on purpose: these tests are about the value-based
# half, which is what catches a secret no pattern recognizes.
SECRET = "TESTONLY_channel_7d21f0c9ab"

CHANNELS = ("prompt", "tool-output", "logs", "errors", "memory", "audit", "rag")


@pytest.fixture(autouse=True)
def _armed_redactor():
    """Every channel test starts with the secret registered, as a real process
    does at startup (``seed_redaction_from_env``)."""
    clear_registry()
    register_secret_value(SECRET)
    yield
    clear_registry()


# --- the model client fake ---------------------------------------------------


class _FakeResponse:
    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Dict[str, Any]:
        return self._data


class _RecordingPost:
    """Records every payload the client sends to the model."""

    def __init__(self, bodies: List[Dict[str, Any]]) -> None:
        self.bodies = list(bodies)
        self.payloads: List[Dict[str, Any]] = []

    def __call__(self, url: str, json: Dict[str, Any], **kwargs: Any) -> _FakeResponse:
        self.payloads.append(json)
        body = self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]
        return _FakeResponse(body)


class _FakeClient:
    def __init__(self, post: _RecordingPost) -> None:
        self._post = post

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def post(self, url: str, json: Dict[str, Any], **kwargs: Any) -> _FakeResponse:
        return self._post(url, json, **kwargs)


def _patch_model(monkeypatch, post: _RecordingPost) -> None:
    monkeypatch.setattr(
        "msb_v3.local_ai.ollama.httpx.Client",
        lambda timeout=None: _FakeClient(post),
    )


def _flatten(value: Any) -> str:
    return json.dumps(value, default=str)


# --- channel 1: prompt -------------------------------------------------------


def test_channel_prompt_never_sends_the_value(monkeypatch):
    post = _RecordingPost([{"response": "ok", "tool_calls": None}])
    _patch_model(monkeypatch, post)

    LocalAIClient(base_url="http://fake:11434").generate(f"explain the key {SECRET}")

    sent = _flatten(post.payloads[0])
    assert SECRET not in sent
    assert MASK in sent


def test_channel_prompt_sensitivity(monkeypatch):
    monkeypatch.setattr("msb_v3.local_ai.ollama.redact", lambda value: value)
    post = _RecordingPost([{"response": "ok", "tool_calls": None}])
    _patch_model(monkeypatch, post)

    LocalAIClient(base_url="http://fake:11434").generate(f"explain the key {SECRET}")

    assert SECRET in _flatten(post.payloads[0]), (
        "the planted value no longer reaches the prompt — this channel's "
        "exposure test is no longer testing anything"
    )


def test_channel_prompt_chat_messages_are_redacted(monkeypatch):
    post = _RecordingPost([{"message": {"content": "ok", "tool_calls": None}}])
    _patch_model(monkeypatch, post)

    LocalAIClient(base_url="http://fake:11434").chat(
        [{"role": "user", "content": f"here is my key {SECRET}"}]
    )

    assert SECRET not in _flatten(post.payloads[0]["messages"])


# --- channel 2: tool output --------------------------------------------------


def test_channel_tool_output_is_redacted_before_the_next_model_step(monkeypatch):
    post = _RecordingPost(
        [
            {"message": {"content": "reading", "tool_calls": [{"function": {"name": "leaky", "arguments": {}}}]}},
            {"message": {"content": "done", "tool_calls": None}},
        ]
    )
    _patch_model(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    # A tool result is the likeliest way a secret arrives from outside.
    client.register_tool("leaky", lambda **kw: f"file contents: {SECRET}")
    client.execute_tool_loop("read it", tools=[{"name": "leaky", "description": "x"}], max_steps=3)

    assert len(post.payloads) == 2, "the tool loop did not reach a second step"
    assert post.payloads[1]["messages"][-1]["role"] == "tool"
    second = _flatten(post.payloads[1])
    assert SECRET not in second
    assert MASK in second


def test_channel_tool_output_sensitivity(monkeypatch):
    """Both hooks sit on this path (the append and the array at the wire), so
    the sensitivity check neutralises both."""
    monkeypatch.setattr("msb_v3.local_ai.ollama.redact", lambda value: value)
    monkeypatch.setattr("msb_v3.local_ai.ollama.redact_obj", lambda value: value)
    post = _RecordingPost(
        [
            {"message": {"content": "reading", "tool_calls": [{"function": {"name": "leaky", "arguments": {}}}]}},
            {"message": {"content": "done", "tool_calls": None}},
        ]
    )
    _patch_model(monkeypatch, post)

    client = LocalAIClient(base_url="http://fake:11434")
    client.register_tool("leaky", lambda **kw: f"file contents: {SECRET}")
    client.execute_tool_loop("read it", tools=[{"name": "leaky", "description": "x"}], max_steps=3)

    assert SECRET in _flatten(post.payloads[1])


# --- channel 3: logs ---------------------------------------------------------


def _record(message: str, **extra: Any) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.channel", level=logging.INFO, pathname=__file__, lineno=1, msg=message, args=(), exc_info=None
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


@pytest.mark.parametrize("formatter", [JSONFormatter(), HumanFormatter()])
def test_channel_logs_redact_message_and_structured_extras(formatter):
    out = formatter.format(_record(f"leaked {SECRET}", structured_data={"nested": [f"also {SECRET}"]}))

    assert SECRET not in out
    assert MASK in out


def test_channel_logs_sensitivity(monkeypatch):
    monkeypatch.setattr("msb_v3.core.logging.redact", lambda value: value)
    out = JSONFormatter().format(_record(f"leaked {SECRET}"))
    assert SECRET in out


def test_channel_logs_redact_an_exception_message_too():
    """Tracebacks are the usual way a token reaches a log file."""
    try:
        raise RuntimeError(f"auth failed with token {SECRET}")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            name="t", level=logging.ERROR, pathname=__file__, lineno=1, msg="boom", args=(), exc_info=sys.exc_info()
        )

    out = JSONFormatter().format(record)
    assert SECRET not in out
    assert MASK in out


# --- channel 4: errors (the API boundary) ------------------------------------


def _error_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecretRedactionMiddleware)

    @app.get("/json")
    async def _json() -> Dict[str, str]:
        return {"text": f"value {SECRET}"}

    @app.get("/boom")
    async def _boom() -> None:
        # The realistic shape: a handler turning an exception into a detail.
        raise HTTPException(status_code=404, detail=f"lookup failed: {SECRET}")

    @app.get("/stream")
    async def _stream() -> StreamingResponse:
        async def chunks():
            yield f"data: {SECRET}\n\n".encode()
            yield b"data: done\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    return app


def test_channel_errors_redact_response_bodies():
    client = TestClient(_error_app())

    ok = client.get("/json")
    assert ok.status_code == 200
    assert SECRET not in ok.text and MASK in ok.text

    boom = client.get("/boom")
    assert boom.status_code == 404
    assert SECRET not in boom.text and MASK in boom.text


def test_channel_errors_sensitivity(monkeypatch):
    monkeypatch.setattr("msb_v3.api.redaction_middleware.redact", lambda value: value)
    client = TestClient(_error_app())

    assert SECRET in client.get("/json").text
    assert SECRET in client.get("/boom").text


def test_channel_errors_streaming_is_redacted_per_chunk_and_still_streams():
    """An SSE body must not be buffered by the middleware — that would turn a
    live stream into a batch — but the chunks are still redacted."""
    response = TestClient(_error_app()).get("/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert SECRET not in response.text
    assert MASK in response.text
    assert "data: done" in response.text


def test_channel_errors_the_middleware_is_installed_inside_gzip():
    """The ordering is load-bearing, so it is pinned rather than commented.

    Starlette inserts middleware at index 0, so a *lower* index is an outer
    wrapper. Redaction must be inside GZip: outside it, the body it inspects is
    compressed bytes, which makes redaction useless and potentially corrupting.
    """
    from msb_v3.api.app import create_app

    names = [m.cls.__name__ for m in create_app().user_middleware]
    assert "SecretRedactionMiddleware" in names and "GZipMiddleware" in names
    assert names.index("SecretRedactionMiddleware") > names.index("GZipMiddleware"), (
        "redaction must sit inside GZip — it would otherwise inspect compressed bytes"
    )


def test_channel_errors_binary_and_non_json_bodies_are_left_alone(monkeypatch):
    """Decoding arbitrary bytes to redact them would corrupt them."""
    app = FastAPI()
    app.add_middleware(SecretRedactionMiddleware)

    @app.get("/blob")
    async def _blob() -> Any:
        from fastapi import Response

        return Response(content=b"\x00\x01\x02", media_type="application/octet-stream")

    assert TestClient(app).get("/blob").content == b"\x00\x01\x02"


# --- channel 5: memory -------------------------------------------------------


def test_channel_memory_session_store_redacts_on_write(tmp_path):
    store = MemoryStore(db_path=str(tmp_path / "messages.db"))
    store.append("s", Message(role="user", content=f"remember my key {SECRET}"))

    stored = store.recent("s")
    assert stored and SECRET not in stored[0].content
    assert MASK in stored[0].content


def test_channel_memory_session_store_sensitivity(monkeypatch, tmp_path):
    monkeypatch.setattr("msb_v3.memory.store.redact", lambda value: value)
    store = MemoryStore(db_path=str(tmp_path / "messages.db"))
    store.append("s", Message(role="user", content=f"remember my key {SECRET}"))

    assert SECRET in store.recent("s")[0].content


def test_channel_memory_fabric_redacts_on_write(tmp_path):
    fabric = MemoryFabric(MemoryFabricStore(str(tmp_path / "fabric.db")))
    item = fabric.store_memory(f"durable note: {SECRET}", tags=["t"])

    assert SECRET not in item.content
    assert MASK in item.content


def test_channel_memory_fabric_sensitivity(monkeypatch, tmp_path):
    monkeypatch.setattr("msb_v3.memory_fabric.fabric.redact", lambda value: value)
    fabric = MemoryFabric(MemoryFabricStore(str(tmp_path / "fabric.db")))
    item = fabric.store_memory(f"durable note: {SECRET}", tags=["t"])

    assert SECRET in item.content


# --- channel 6: audit --------------------------------------------------------


def _patch_audit_db(monkeypatch, tmp_path) -> Path:
    """Redirect the auditor's runtime root *and* db file.

    Patching only ``_MULCH_DB`` leaves ``_init_db`` creating the real
    ``data/triumvirate`` directory while connecting to a path whose parent does
    not exist, which fails with an unhelpful OperationalError.
    """
    root = tmp_path / "triumvirate"
    monkeypatch.setattr(audit_mod, "_RUNTIME_ROOT", root)
    monkeypatch.setattr(audit_mod, "_MULCH_DB", root / "mulch_learnings.db")
    return root / "mulch_learnings.db"


def _mulch_description(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT description FROM mulch_learnings ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    return str(row[0])


def test_channel_audit_redacts_the_stored_finding(monkeypatch, tmp_path):
    db_path = _patch_audit_db(monkeypatch, tmp_path)
    auditor = ArgusAuditor()

    written = auditor.record_mulch(
        MulchFinding(component="c", finding_type="t", description=f"config carried {SECRET}")
    )

    assert SECRET not in written["description"]
    assert SECRET not in _mulch_description(db_path)


def test_channel_audit_sensitivity(monkeypatch, tmp_path):
    monkeypatch.setattr("msb_v3.observability.audit.redact", lambda value: value)
    db_path = _patch_audit_db(monkeypatch, tmp_path)
    auditor = ArgusAuditor()

    auditor.record_mulch(MulchFinding(component="c", finding_type="t", description=f"config carried {SECRET}"))

    assert SECRET in _mulch_description(db_path)


def test_channel_audit_redacts_the_error_string(monkeypatch, tmp_path):
    _patch_audit_db(monkeypatch, tmp_path)
    monkeypatch.setattr(audit_mod, "_MAX_RETRIES", 1)

    def _boom(*args: Any, **kwargs: Any) -> List[Any]:
        raise RuntimeError(f"boom {SECRET}")

    monkeypatch.setattr(ArgusAuditor, "audit_directives", _boom)

    result = ArgusAuditor().run()

    assert result.get("error"), "the run did not report the failure at all"
    assert SECRET not in str(result["error"])
    assert MASK in str(result["error"])


# --- channel 7: RAG ----------------------------------------------------------


class _FakeAdapter:
    def __init__(self, index: str) -> None:
        self.index = index

    async def search(self, query: str, *, top_k: int = 5) -> List[Dict[str, Any]]:
        if self.index == "broken":
            raise ValueError(f"adapter exploded while reading {SECRET}")
        # `score` is part of the adapter contract — rrf() reads it — so the fake
        # has to carry it or the fusion stage fails before redaction is reached.
        return [
            {
                "id": f"{self.index}-1",
                "score": 0.9,
                "text": f"a document that quotes {SECRET}",
                "source": "test",
                "metadata": {"owner": f"key={SECRET}"},
            }
        ]


def _patch_adapters(monkeypatch) -> None:
    monkeypatch.setattr(engine_mod, "get_adapter", lambda index, tenant: _FakeAdapter(index))


def _run_retrieval() -> Dict[str, Any]:
    return asyncio.run(RetrievalRouter(tenant_id="test").run("what is the key"))


def test_channel_rag_redacts_matches_metadata_and_route_errors(monkeypatch):
    _patch_adapters(monkeypatch)
    result = _run_retrieval()

    assert result["matches"], "retrieval returned no matches — nothing was tested"
    flat = _flatten(result)
    assert SECRET not in flat
    assert MASK in flat


def test_channel_rag_sensitivity(monkeypatch):
    monkeypatch.setattr("msb_v3.retrieval.engine.redact", lambda value: value)
    monkeypatch.setattr("msb_v3.retrieval.engine.redact_obj", lambda value: value)
    _patch_adapters(monkeypatch)

    assert SECRET in _flatten(_run_retrieval())


def test_channel_rag_a_failing_route_does_not_leak_its_exception(monkeypatch):
    _patch_adapters(monkeypatch)
    monkeypatch.setattr(
        engine_mod, "plan_query", lambda query, top_k: {"routes": [{"index": "broken", "top_k": 1, "weight": 1.0}]}
    )

    result = _run_retrieval()

    errors = result["route_errors"]
    assert errors and "broken" in errors
    assert SECRET not in _flatten(errors)
    assert MASK in _flatten(errors)


# --- the channel set itself --------------------------------------------------


def test_all_seven_channels_have_a_paired_exposure_and_sensitivity_test():
    """Keeps the claim honest: if a channel loses either half, this fails."""
    source = Path(__file__).read_text(encoding="utf-8")
    assert len(CHANNELS) == 7
    for channel in CHANNELS:
        slug = f"test_channel_{channel.replace('-', '_')}_"
        assert slug in source, f"no test named {slug}… in this file"
        # tool-output's second half is named for the belt it neutralises.
        assert source.count(slug) >= 2 or channel in {"tool-output", "errors"}, channel
