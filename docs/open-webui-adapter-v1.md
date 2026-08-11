# Open WebUI adapter (OpenAI-compatible /v1)

Open WebUI is the ready-made, ChatGPT-style front end for MSB v3. MSB stays
the brain — the UI talks to it through a small OpenAI-compatible adapter:

```text
Open WebUI UI  --OpenAI-compat-->  /v1  (msb-v3)  -->  ChatHarness  -->  Ollama / llama.cpp / Qdrant / COS
```

Nothing in MSB is replaced; the adapter is a new `/v1` router mounted on the
same server (`src/msb_v3/api/openai_compat.py`).

## What the adapter adds

| Endpoint | Purpose |
|---|---|
| `GET /v1/models` | model dropdown (the two configured backends + the embedding model) |
| `POST /v1/chat/completions` | OpenAI ChatCompletion JSON, or SSE when `stream: true` |
| `POST /v1/embeddings` | OpenAI-compatible embeddings for document RAG, backed by the native `/rag` provider (Ollama `nomic-embed-text`, 768d, incl. long-text truncation retries) |

Request mapping (OpenAI → MSB native contract):

- `messages[].role == "system"` → `ChatRequest.system` (last wins)
- last `user` message → `ChatRequest.query`
- prior messages → `ctx["history"]`
- `tools[]` → `ChatRequest.tools` (OpenAI function defs flattened onto the
  native `ToolSpec` shape, so the harness tool loop runs MSB's vault/memory/
  research tools)
- `user` → MSB session (sanitized; default `openai-ui`), `X-Tenant-ID` →
  tenant scoping — same behavior as the native `/chat`

## Auth (fail-closed)

- Adapter key: `OPENAI_API_KEY` env (`core/config.py` → `settings.openai_api_key`).
- **Unset** → `/v1` returns `503` ("adapter is closed") — nothing leaks.
- **Set** → clients must send `Authorization: Bearer <key>`; anything else
  is `401`.
- The key is read per-request, so a `.env` change applies without restart.

## Run Open WebUI

1. Set the key (and optional COS vars) in `.env`:

   ```bash
   OPENAI_API_KEY=<any random string>
   # /v1/embeddings guards (optional — defaults shown):
   OPENAI_EMBED_MAX_BATCH=32        # max items per request (413 beyond)
   OPENAI_EMBED_RATE_MAX=120        # max items/client/window (429 beyond)
   OPENAI_EMBED_RATE_WINDOW_S=60
   # optional — Tencent COS for file uploads:
   TENCENT_COS_SECRET_ID=...
   TENCENT_COS_SECRET_KEY=...
   TENCENT_COS_ENDPOINT=https://cos.ap-guangzhou.myqcloud.com
   TENCENT_COS_REGION=ap-guangzhou
   TENCENT_COS_BUCKET=...
   ```

2. Start the UI (Docker Desktop on this Mac; `host.docker.internal` resolves
   to the host automatically):

   ```bash
   docker compose -f docker-compose.sovereign.yml up -d open-webui
   # UI: http://localhost:3001   (host port 3001 — Grafana owns :3000)
   ```

3. In Open WebUI: **⚙️ Admin Settings → Connections → OpenAI API → Add
   Connection**:
   - **API Base URL**: `http://host.docker.internal:8766/v1` (no trailing slash — it breaks `/v1/models` discovery)
   - **API Key**: the same `OPENAI_API_KEY` value
   - Save; the two MSB models appear in the model dropdown.

`ENABLE_OLLAMA_API=false` is already set in the compose service, so every
message routes through MSB's harness (including the tool loop) rather than
Open WebUI calling Ollama directly.

## Document RAG → MSB embeddings

Uploaded-file RAG is wired to embed through the adapter, not Open WebUI's
default local sentence-transformers model:

```bash
RAG_EMBEDDING_ENGINE=openai         # routes to the OpenAI-compatible connection
RAG_EMBEDDING_MODEL=nomic-embed-text # must be a model Ollama has pulled (768d)
```

The engine POSTs to `{base_url}/embeddings` with `{"input": [...], "model":
...}` — exactly `POST /v1/embeddings` on MSB. `RAG_OPENAI_API_BASE_URL` /
`RAG_OPENAI_API_KEY` fall back to the `OPENAI_*` values already set in the
compose service, so no extra secrets are needed. The `/v1/embeddings` guards
(413 batch cap, 429 per-client rate) apply to Open WebUI's chunking the same
as to any other caller.

> **Gotcha — persisted config beats env.** On the *first* boot Open WebUI
> seeds its `config` table (`webui.db`) with defaults (`rag.embedding_engine`
> empty, sentence-transformers). Those DB rows **override** the compose env
> vars, so a fresh install embeds with the local 384-d model even after this
> compose change. To flip it, either use the UI (Admin Settings → Documents →
> Embedding) or, once admin:
>
> ```bash
> curl -X POST http://localhost:3001/api/v1/retrieval/embedding/update \
>   -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
>   -d '{"RAG_EMBEDDING_ENGINE":"openai","RAG_EMBEDDING_MODEL":"nomic-embed-text",
>        "openai_config":{"url":"http://host.docker.internal:8766/v1","key":"$OPENAI_API_KEY"}}'
> ```
>
> Then delete/re-upload old files: collections created at 384-d are
> incompatible with the new 768-d vectors.

## File storage → Tencent COS

The compose service sets `STORAGE_PROVIDER=s3` with `S3_*` vars sourced from
`TENCENT_COS_*`. COS is S3-compatible, so uploaded files land in your bucket
once the credentials are filled in; while they're empty, files stay on the
`open_webui_data` volume instead (no code change needed to switch).

## Verify

```bash
# adapter auth (expect 503 with no key, 200 with it)
curl -s http://127.0.0.1:8766/v1/models
curl -s -H "Authorization: Bearer $OPENAI_API_KEY" http://127.0.0.1:8766/v1/models

# a chat completion
curl -s -H "Authorization: Bearer $OPENAI_API_KEY" http://127.0.0.1:8766/v1/chat/completions \
  -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"hello"}]}' -H 'Content-Type: application/json'

# an embedding (document RAG)
curl -s -H "Authorization: Bearer $OPENAI_API_KEY" http://127.0.0.1:8766/v1/embeddings \
  -d '{"input":"text to embed"}' -H 'Content-Type: application/json'
```

Tests: `pytest tests/test_openai_compat.py -q` (fake harness; no model calls).

## Known limitations (v1)

- The harness's tool loop is single-shot: multi-turn continuity relies on
  MSB's memory store per session (exactly like the native `/chat`), not on
  the message array Open WebUI sends.
- Streaming is a single SSE delta containing the full answer (`[DONE]`
  immediately after) — MSB generates the whole text before responding.
- `n > 1` is not implemented.
- Embeddings are guarded (per-request batch cap `OPENAI_EMBED_MAX_BATCH`
  → 413, per-client items-per-window cap `OPENAI_EMBED_RATE_MAX` → 429) but
  still sequential — fine for document chunking, not a bulk-inference
  endpoint. Both caps are in-process (single worker), like the existing
  `/research/assistant/run` limiter.
