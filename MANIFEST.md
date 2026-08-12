# MSB v3 — Runtime Manifest

**Everything MSB v3 needs to run.** This is the runtime bill-of-materials — the
services, models, dependencies, environment, and data stores the *running*
system depends on. For what the GitHub repo/CI must provide, see
[`REPO_REQUIREMENTS.md`](REPO_REQUIREMENTS.md).

- **Verified:** 2026-08-11 against the live machine (`darwin`, this host).
- **Legend:** ✅ present & verified · ⚠️ configured-but-missing / optional-not-provisioned · 🔒 secret/unset.
- **Source of truth:** [`src/msb_v3/core/config.py`](src/msb_v3/core/config.py) — every env var and default below is declared there. Nothing here is invented; each row traces to a file I read.

---

## 1. Identity & entry point

| Field | Value | Source |
|---|---|---|
| Name / version | `msb-v3` `0.1.0` | `pyproject.toml` |
| Python | `>=3.11` (host: `/opt/homebrew/Caskroom/miniforge/base/bin/python`) | `pyproject.toml`, CLAUDE.md |
| Console script | `msb-v3 = msb_v3.__main__:run` | `pyproject.toml` |
| ASGI app | `msb_v3.__main__:app` (uvicorn) | `src/msb_v3/__main__.py` |
| Package layout | `src/` (setuptools find) | `pyproject.toml` |

---

## 2. Python dependencies (pinned)

**Runtime** (`pyproject.toml` → `dependencies`):

| Package | Pin |
|---|---|
| fastapi | `0.141.1` |
| uvicorn[standard] | `0.34.1` |
| pydantic | `2.10.6` |
| httpx | `0.28.1` |
| prometheus-client | `0.21.1` |
| qdrant-client | `1.18.0` |

**Dev** (`optional-dependencies.dev`): `pytest 8.3.5`, `pytest-asyncio 0.24.0`, `ruff 0.9.4`.

> `pydantic-settings` was dropped 2026-08-11 — imported nowhere (`grep` clean), config is env-var-first.

---

## 3. External services & binaries

| Service | Port | Binary | Supervision | Status (2026-08-11) |
|---|---|---|---|---|
| **msb-v3** (this app) | `8766` | uvicorn | launchd `com.lordwilson.msb-v3` (KeepAlive) | ✅ up (HTTP 200) |
| **Ollama** (LLM + embeddings) | `11434` | `/opt/homebrew/bin/ollama` | manual / brew service | ✅ up |
| **Qdrant** (vector store) | `6333` | `/Users/lordwilson/.local/bin/qdrant` | launchd `com.lordwilson.qdrant` (KeepAlive) | ✅ up — collection `tenant_wilson-vault` |
| **llama.cpp** (alt backend) | `8080` | `/opt/homebrew/bin/llama-server` | manual | ⚠️ optional; not the active backend (`MSB_ACTIVE_BACKEND=ollama`) |

**Qdrant footgun (from CLAUDE.md):** must be launched from repo root — this
build has no `--storage-path`; it resolves `./storage` relative to cwd.
Launching elsewhere silently creates empty storage. Recovery: `scripts/start-qdrant.sh start`.

---

## 4. Models

| Role | Model | Where | Status |
|---|---|---|---|
| Default chat (config) | `qwen3:8b` (5.2 GB) | Ollama | ✅ default now resolves to an installed model (was `deepseek-r1:1.5b`, fixed 2026-08-11) |
| Embeddings | `nomic-embed-text:latest` (274 MB) | Ollama | ✅ present |
| llama.cpp weights | `~/models/gemma-4-12b-it/gemma-4-12b-it-q4_k_m.gguf` | disk | ⚠️ **NOT present** (only needed if llama.cpp backend is used) |

> Resolved: default is `qwen3:8b`, and `.env.example` aligned to the same (was `qwen3:latest`, also not pulled).

---

## 5. Environment variables

All read in `config.py`; defaults shown. `.env.example` also carries adjacent-layer
keys (Open WebUI / storage / RAG) marked *(ext)*.

| Var | Default | Purpose |
|---|---|---|
| `MSB_HOST` | `127.0.0.1` | bind host |
| `MSB_PORT` | `8766` | bind port |
| `MSB_RELOAD` | `0` | uvicorn autoreload |
| `MSB_REASONING_SCORER` | `1` | enable reasoning scorer |
| `MSB_HOME` / `MSB_REPO` | derived from file location | repo root override (CI sets `MSB_REPO`) |
| `MSB_VAULT_PATH` | `~/Documents/Vault` | Obsidian vault root (user data) |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `qwen3:8b` | default chat model (installed ✅) |
| `MSB_ACTIVE_BACKEND` | `ollama` | active inference backend |
| `MSB_DB_PATH` | `data/msb_v3.db` | primary SQLite DB |
| `MSB_LOG_LEVEL` | `info` | log level |
| `MSB_CORS_ORIGINS` | `*` | CORS allowlist |
| `MSB_REQUEST_TIMEOUT_S` | `60.0` | upstream request timeout |
| `LLAMA_CPP_URL` | `http://127.0.0.1:8080` | llama.cpp endpoint (opt) |
| `LLAMA_CPP_MODEL` | `~/models/gemma-4-12b-it/...q4_k_m.gguf` | llama.cpp weights (opt) ⚠️ |
| `NOTEBOOKLM_ACTIVE_INDEX` | `~/notebooklm-library-deep-dive/active-index.json` | NotebookLM cluster index |
| `TAVILY_API_KEY` | `""` | ✅ web search — powers the flywheel's arxiv paper feed (Phase 2b, set in `.env`) |
| `MSB_OPERATOR_TOKEN` | `""` | ✅ operator bearer token — gates the /governance + /flywheel control endpoints (Phase 3, set in `.env` via `scripts/set-operator-token.sh`) |
| `OPENAI_API_KEY` | `""` | 🔒 bearer for `/v1` adapter — **empty = adapter fail-closed (503)** |
| `OPENAI_EMBED_MAX_BATCH` | `32` | `/v1/embeddings` per-request cap (413 over) |
| `OPENAI_EMBED_RATE_MAX` | `120` | sliding-window item cap (429 over) |
| `OPENAI_EMBED_RATE_WINDOW_S` | `60` | rate window seconds |
| `RAG_EMBEDDING_ENGINE` / `RAG_EMBEDDING_MODEL` *(ext)* | — | Open WebUI RAG embedding config |
| `OLLAMA_EMBED_MODEL` *(ext)* | — | embedding model for RAG layer |
| `STORAGE_PROVIDER` / `TENCENT_COS_*` *(ext)* | — | 🔒 object-storage provider (Tencent COS) |
| `WEBUI_SECRET_KEY` *(ext)* | — | 🔒 Open WebUI session secret |

---

## 6. Data stores & paths

| Store | Path | Kind | Status |
|---|---|---|---|
| Primary DB | `data/msb_v3.db` | SQLite | ✅ |
| Research runs | `runtime/research/` | dir of run folders | ✅ |
| Argus mulch learnings | `runtime/triumvirate/mulch_learnings.db` | SQLite | referenced by `api/home.py` |
| Triumvirate mission anchor | `runtime/triumvirate/` (MissionAnchor) | file | ✅ (`hash=d96c8559b768`, goal="sovereign cluster deploy") |
| Qdrant storage | `storage/` (repo-root relative) | Qdrant | ✅ `tenant_wilson-vault` (~5.4k chunks per CLAUDE.md) |
| Vault (user data) | `~/Documents/Vault` | Obsidian / git | ✅ |
| Standby pidfiles | `.artifacts/msb-v3.pid`, `.artifacts/qdrant.pid` | pidfile | ✅ |

---

## 7. Supervision (launchd)

- `scripts/launchd/com.lordwilson.msb-v3.plist` → runs `scripts/run.sh` (app supervisor), `KeepAlive`. Installed copy in `~/Library/LaunchAgents/` (keep in sync).
- `scripts/launchd/com.lordwilson.qdrant.plist` → runs qdrant with `WorkingDirectory=$REPO`, `KeepAlive`.
- Control: `scripts/start.sh` / `scripts/stop.sh` (launchd-aware, nohup fallback on SSH). Verify: `launchctl print gui/$(id -u)/com.lordwilson.msb-v3`.

---

## 8. Operational commands (Makefile)

`make test` · `make server` · `make smoke` · `make server-start|stop|status` ·
`make hygiene` (full battery + Qdrant test-collection sweep) ·
`make qdrant-start|stop|status|sweep` · `scripts/harness-gate-dryrun.sh`.

---

## 9. Gaps — what "everything it needs" does NOT yet have

Honest ledger; these are why v3 is operational-for-you but not walk-away-done:

1. ✅ ~~Default model missing~~ — **fixed 2026-08-11**: default is now `qwen3:8b` (installed); `.env.example` aligned.
2. ⚠️ **llama.cpp weights missing** — the `:8080` backend can't start as configured. Fine while `MSB_ACTIVE_BACKEND=ollama`, but the fallback isn't real.
3. 🔒 **One secret unset** — `OPENAI_API_KEY` (adapter is 503 until set). `TAVILY_API_KEY` set (flywheel feed), `MSB_OPERATOR_TOKEN` set (control-surface auth, Phase 3). Expected for local, required before exposing.
4. ✅ ~~Dead dep~~ — **fixed 2026-08-11**: `pydantic-settings` removed (imported nowhere).
5. 📦 **No `Dockerfile`** — CI docker job is inert until one exists (`REPO_REQUIREMENTS.md`); no container path to run this off this Mac.
6. 🌿 **~40 files uncommitted** at manifest time — the running system is ahead of `main`; reproducibility from a fresh clone is not guaranteed until landed.

---
*Generated by inspecting `pyproject.toml`, `config.py`, `__main__.py`, launchd plists, and live probes of :8766 / :6333 / Ollama. Not derived from memory.*
