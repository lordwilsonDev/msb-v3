"""Regression tests for the RAG index point-ID + embed-retry fixes.

History: /rag/index used the in-batch index as the Qdrant point ID, so every
15-doc batch overwrote ids 0..14 -- the collection could never exceed
BATCH_SIZE points no matter how many documents were submitted (wilson-vault
was silently capped at ~171 points while the vault grew past 2000 files).
These tests lock in the stable-ID scheme and the embed truncation retry.

No live Ollama/Qdrant needed: _embed is monkeypatched; the ID logic is pure.
"""
from __future__ import annotations

import pytest

from msb_v3.api import rag


def test_stable_point_id_is_deterministic_and_distinct():
    a0 = rag._stable_point_id("notes/a.md", 0)
    assert a0 == rag._stable_point_id("notes/a.md", 0)  # idempotent reindex
    assert a0 != rag._stable_point_id("notes/a.md", 1)  # chunk varies
    assert a0 != rag._stable_point_id("notes/b.md", 0)  # source varies
    # Qdrant-valid format: UUID or unsigned integer
    assert len(a0) == 36 and a0.count("-") == 4


def test_batch_ids_do_not_collide_across_batches():
    """The old bug: each batch reused ids 0..14, so batch N+1 overwrote batch N.

    Simulate two batches of 15 with distinct sources and assert all 30 point
    IDs are unique -- the collection can then hold all documents.
    """
    batch_a = [{"source": f"f{i}.md", "chunk": 0, "text": "x"} for i in range(15)]
    batch_b = [{"source": f"g{i}.md", "chunk": 0, "text": "y"} for i in range(15)]
    ids_a = {str(d.get("id") or rag._stable_point_id(d["source"], d["chunk"])) for d in batch_a}
    ids_b = {str(d.get("id") or rag._stable_point_id(d["source"], d["chunk"])) for d in batch_b}
    assert len(ids_a) == 15
    assert len(ids_b) == 15
    assert ids_a.isdisjoint(ids_b)  # no cross-batch collision


@pytest.mark.asyncio
async def test_embed_truncates_and_retries_on_context_length(monkeypatch):
    """A context-length 500 must truncate+retry, not fail the whole batch."""

    calls: list[str] = []

    async def fake_post(*args, **kwargs):
        prompt = kwargs.get("json", {}).get("prompt", "")
        calls.append(prompt)
        # First call fails with context length (like Ollama on a long prompt);
        # after truncation the retry succeeds (like real Ollama on short text).
        if len(calls) == 1:

            class FailResp:
                status_code = 500
                text = '{"error":"the input length exceeds the context length"}'

                def raise_for_status(self):
                    import types

                    import httpx

                    err = httpx.HTTPStatusError("ctx", request=types.SimpleNamespace(), response=types.SimpleNamespace(text=self.text))
                    raise err

            return FailResp()

        class OkResp:
            status_code = 200
            text = ""

            def raise_for_status(self):
                pass

            def json(self):
                # 768-dim vector
                return {"embedding": [0.1] * rag._EMBED_DIM}

        return OkResp()

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return await fake_post(*a, **kw)

    monkeypatch.setattr(rag.httpx, "AsyncClient", FakeClient)

    vec = await rag._embed("word " * 2000)  # ~10k chars, over the token ceiling
    assert len(vec) == rag._EMBED_DIM
    # It must have truncated at least once (calls > 1) and the final prompt
    # must be strictly shorter than the original.
    assert len(calls) > 1
    assert len(calls[-1]) < len("word " * 2000)


@pytest.mark.asyncio
async def test_embed_raises_after_truncation_floor(monkeypatch):
    """Even at the minimum size a real failure must surface, not hang."""

    async def fake_post(*args, **kwargs):
        class Resp:
            status_code = 500
            text = '{"error":"the input length exceeds the context length"}'

            def raise_for_status(self):
                import types

                import httpx

                err = httpx.HTTPStatusError("ctx", request=types.SimpleNamespace(), response=types.SimpleNamespace(text=self.text))
                raise err

        return Resp()

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return await fake_post(*a, **kw)

    monkeypatch.setattr(rag.httpx, "AsyncClient", FakeClient)

    with pytest.raises(RuntimeError, match="embedding failed"):
        await rag._embed("tiny")  # under the 500-char floor -> final attempt fails
